from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from app.core.clock import utc_now
from app.core.config import Settings
from app.integrations.grok2api.client import Grok2APIClient, IntegrationError
from app.integrations.proxy1024 import (
    Proxy1024Error,
    fetch_proxies,
    generate_gateway_proxies,
)
from app.integrations.resin import ResinClient, ResinError
from app.persistence.proxy_pool_repository import STATUS_ACTIVE, ProxyPoolRepository

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60.0
DEFAULT_PREFIX = "grokiq-1024"
DEFAULT_PLATFORM_PREFIX = "g1024"


class ProxyPoolService:
    """Pull short-lived proxies from 1024proxy and publish them into Resin.

    Every batch of ``group_size`` proxies becomes one Resin ``local``
    subscription named ``<prefix>-NN``. The service records the Resin
    subscription id so later refreshes replace the batch content in place
    instead of creating duplicate subscriptions.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ProxyPoolRepository,
        resin: ResinClient | None = None,
        grok: Grok2APIClient | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.resin = resin or ResinClient(settings)
        self.grok = grok
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._worker(), name="proxy-pool-refresh")
        self._wake.set()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def list_groups(self) -> dict[str, Any]:
        raw = self.repository.list_groups()
        enabled_map: dict[str, bool] = {}
        if self.settings.proxy_pool_auto_egress and self.grok is not None:
            try:
                payload = await self.grok.list_egress_nodes(
                    scope="grok_build", pageSize=1000
                )
                for node in payload.get("items") or []:
                    if isinstance(node, dict) and node.get("name"):
                        enabled_map[str(node["name"])] = bool(node.get("enabled"))
            except IntegrationError:
                enabled_map = {}
        groups = [
            self._public_group(
                group,
                egress_enabled=enabled_map.get(
                    str(group.get("egress_node_name") or ""), None
                ),
            )
            for group in raw
        ]
        return {
            "groups": groups,
            "total": len(groups),
            "groupSize": self.settings.proxy_pool_group_size,
            "leaseHours": self.settings.proxy_pool_lease_hours,
            "prefix": self._prefix(),
            "platformPrefix": self._platform_prefix(),
            "autoEgress": self.settings.proxy_pool_auto_egress,
        }

    async def preview(self, *, total: int | None = None) -> dict[str, Any]:
        count = self._resolve_target(total)
        proxies = await self._fetch(self._fetch_count(count))
        plan = self._plan(proxies)
        return {
            "requested": count,
            "fetched": len(proxies),
            "mode": self._mode(),
            "groupSize": self.settings.proxy_pool_group_size,
            "groupCount": len(plan),
            "groups": [
                {
                    "index": index,
                    "name": name,
                    "size": len(chunk),
                    "sample": chunk[:3],
                }
                for index, name, chunk in plan
            ],
        }

    async def import_groups(self, *, total: int | None = None) -> dict[str, Any]:
        count = self._resolve_target(total)
        proxies = await self._fetch(self._fetch_count(count))
        plan = self._plan(proxies)
        if not plan:
            raise Proxy1024Error("没有可导入的代理，请检查 1024proxy 提取结果")

        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        created = 0
        updated = 0
        for index, name, chunk in plan:
            try:
                record, action = await self._publish(index, name, chunk)
                results.append(self._public_group(record))
                if action == "created":
                    created += 1
                else:
                    updated += 1
            except (Proxy1024Error, ResinError, ValueError) as exc:
                failures.append({"name": name, "error": str(exc)})
                existing = self.repository.find_by_name(name)
                if existing is not None:
                    self.repository.mark_failed(int(existing["id"]), str(exc))

        pruned = await self._prune_extra(len(plan))
        return {
            "requested": count,
            "fetched": len(proxies),
            "mode": self._mode(),
            "groupCount": len(plan),
            "created": created,
            "updated": updated,
            "failed": len(failures),
            "failures": failures,
            "pruned": pruned,
            "groups": results,
        }

    async def refresh_all(self) -> dict[str, Any]:
        groups = self.repository.list_groups()
        if not groups:
            return await self.import_groups()
        total = max(
            self.settings.proxy_pool_group_size * len(groups),
            self.settings.proxy_pool_group_size,
        )
        return await self.import_groups(total=total)

    async def refresh_group(self, group_id: int) -> dict[str, Any]:
        group = self.repository.get_group(group_id)
        if group is None:
            raise ValueError("代理池分组不存在")
        size = self._chunk_size()
        proxies = await self._fetch(size)
        chunk = proxies[:size]
        record, action = await self._publish(
            int(group.get("group_index") or 0),
            str(group["name"]),
            chunk,
        )
        return {"action": action, "group": self._public_group(record)}

    async def delete_groups(self, *, group_ids: list[int]) -> dict[str, Any]:
        groups = {int(group["id"]): group for group in self.repository.list_groups()}
        removed_remote = 0
        remote_errors: list[dict[str, Any]] = []
        prefix = self._platform_prefix()
        for group_id in dict.fromkeys(int(value) for value in group_ids):
            group = groups.get(group_id)
            if group is None:
                continue
            subscription_id = str(group.get("subscription_id") or "")
            if subscription_id:
                try:
                    await self.resin.delete_subscription(subscription_id)
                    removed_remote += 1
                except ResinError as exc:
                    if not exc.not_found:
                        remote_errors.append(
                            {"name": group.get("name"), "error": str(exc)}
                        )
            platform_name = str(group.get("platform_name") or "")
            node_id = int(group.get("egress_node_id") or 0)
            if node_id > 0 and self.grok is not None:
                try:
                    await self.grok.delete_egress_nodes([node_id])
                except IntegrationError as exc:
                    remote_errors.append(
                        {"name": platform_name or group.get("name"), "error": str(exc)}
                    )
            if platform_name.startswith(prefix):
                try:
                    platforms = await self.resin.list_platforms()
                    target = next(
                        (
                            item
                            for item in platforms
                            if item.get("name") == platform_name
                        ),
                        None,
                    )
                    if target is not None:
                        await self.resin.delete_platform(str(target.get("id") or ""))
                except ResinError as exc:
                    remote_errors.append(
                        {"name": platform_name, "error": str(exc)}
                    )
        result = self.repository.delete_groups(group_ids)
        return {**result, "removedRemote": removed_remote, "remoteErrors": remote_errors}

    async def _publish(
        self, index: int, name: str, chunk: list[str]
    ) -> tuple[dict[str, Any], str]:
        if not chunk:
            raise Proxy1024Error(f"{name} 没有可用代理")
        content = "\n".join(chunk)
        interval = f"{self.settings.proxy_pool_lease_hours}h"
        existing = self.repository.find_by_name(name)
        action = "updated"
        subscription_id = str((existing or {}).get("subscription_id") or "")
        if subscription_id:
            try:
                await self.resin.update_subscription(
                    subscription_id,
                    content=content,
                    update_interval=interval,
                    enabled=True,
                )
            except ResinError as exc:
                if not exc.not_found:
                    raise
                subscription_id = ""
        if not subscription_id:
            created = await self.resin.create_subscription(
                name=name,
                content=content,
                update_interval=interval,
                enabled=True,
            )
            subscription_id = str(created.get("id") or "")
            if not subscription_id:
                raise ResinError("Resin 未返回订阅 ID")
            action = "created"
        lease_expires_at = utc_now() + timedelta(
            hours=self.settings.proxy_pool_lease_hours
        )
        platform_name, egress_node_id = await self._sync_egress(index, name)
        record = self.repository.upsert_group(
            group_index=index,
            name=name,
            subscription_id=subscription_id,
            size=len(chunk),
            scheme=self.settings.proxy_pool_scheme,
            proxies=chunk,
            status=STATUS_ACTIVE,
            error="",
            lease_expires_at=lease_expires_at,
            platform_name=platform_name,
            egress_node_id=egress_node_id,
            egress_node_name=platform_name,
        )
        return record, action

    async def _sync_egress(self, index: int, group_name: str) -> tuple[str, int | None]:
        """Create/update the Resin platform and grok2api egress node for a group."""

        if not self.settings.proxy_pool_auto_egress or self.grok is None:
            return "", None
        platform_name = self._platform_name(index)
        try:
            platforms = await self.resin.list_platforms()
            existing = next(
                (item for item in platforms if item.get("name") == platform_name),
                None,
            )
            regex = [f"^{group_name}/"]
            if existing is None:
                await self.resin.create_platform(
                    name=platform_name, regex_filters=regex
                )
            elif list(existing.get("regex_filters") or []) != regex:
                await self.resin.update_platform(
                    str(existing.get("id") or ""), regex_filters=regex
                )

            token = (self.settings.proxy_pool_resin_proxy_token or "").strip()
            if not token:
                return platform_name, None
            return platform_name, await self._sync_egress_node(platform_name, token)
        except (ResinError, IntegrationError) as exc:
            logger.warning(
                "proxy pool auto egress failed for %s: %s", group_name, exc
            )
            return platform_name, None

    async def _sync_egress_node(
        self, platform_name: str, token: str
    ) -> int | None:
        capacity = (
            int(self.settings.proxy_pool_group_size)
            * int(self.settings.proxy_pool_egress_capacity_factor)
        )
        proxy_url = f"socks5h://{platform_name}.{{account}}:{token}@resin:2260"
        payload = await self.grok.list_egress_nodes(scope="grok_build", pageSize=1000)
        items = payload.get("items") if isinstance(payload, dict) else []
        existing = next(
            (
                node
                for node in (items or [])
                if isinstance(node, dict) and node.get("name") == platform_name
            ),
            None,
        )
        if existing is not None:
            node_id = int(existing.get("id") or 0)
            if node_id <= 0:
                return None
            await self.grok.update_egress_node(
                node_id,
                name=platform_name,
                proxy_pool=True,
                account_capacity=capacity,
                enabled=bool(existing.get("enabled", True)),
                proxy_url=proxy_url,
            )
            return node_id
        created = await self.grok.create_egress_node(
            name=platform_name,
            proxy_url=proxy_url,
            proxy_pool=True,
            account_capacity=capacity,
            enabled=True,
        )
        node_id = int((created or {}).get("id") or 0)
        return node_id or None

    async def set_group_egress(
        self, group_id: int, *, enabled: bool
    ) -> dict[str, Any]:
        group = self.repository.get_group(group_id)
        if group is None:
            raise ValueError("代理池分组不存在")
        node_id = int(group.get("egress_node_id") or 0)
        if node_id <= 0 or self.grok is None:
            raise ValueError("该分组还没有自动创建的出口节点")
        await self.grok.set_egress_nodes_enabled([node_id], enabled)
        updated = self.repository.get_group(group_id) or group
        return self._public_group(updated)

    async def sync_egress_now(self) -> dict[str, Any]:
        if not self.settings.proxy_pool_auto_egress or self.grok is None:
            raise ValueError("未开启自动出口联动")
        synced = 0
        for group in self.repository.list_groups():
            name = str(group.get("name") or "")
            index = int(group.get("group_index") or 0)
            if not name or index <= 0:
                continue
            platform_name, node_id = await self._sync_egress(index, name)
            if platform_name:
                self.repository.set_egress(
                    int(group["id"]),
                    platform_name=platform_name,
                    egress_node_id=node_id,
                    egress_node_name=platform_name,
                )
                synced += 1
        return {"synced": synced, **await self.list_groups()}

    async def _prune_extra(self, keep: int) -> int:
        stale = [
            group
            for group in self.repository.list_groups()
            if int(group.get("group_index") or 0) > keep
        ]
        if not stale:
            return 0
        result = await self.delete_groups(
            group_ids=[int(group["id"]) for group in stale]
        )
        return int(result.get("deleted") or 0)

    def _mode(self) -> str:
        return "gateway" if self.settings.proxy_pool_mode == "gateway" else "url"

    def _fetch_count(self, target: int) -> int:
        if self._mode() == "gateway":
            return target * int(self.settings.proxy_pool_over_factor)
        return target

    def _chunk_size(self) -> int:
        size = int(self.settings.proxy_pool_group_size)
        if self._mode() == "gateway":
            size *= int(self.settings.proxy_pool_over_factor)
        return max(1, size)

    async def _fetch(self, num: int) -> list[str]:
        if self._mode() == "gateway":
            return generate_gateway_proxies(
                count=num,
                host=self.settings.proxy_pool_gateway_host,
                port=self.settings.proxy_pool_gateway_port,
                username=self.settings.proxy_pool_gateway_username,
                password=self.settings.proxy_pool_gateway_password,
                region=self.settings.proxy_pool_gateway_region,
                sticky=self.settings.proxy_pool_gateway_sticky,
                scheme=self.settings.proxy_pool_scheme,
            )
        return await fetch_proxies(
            self.settings.proxy_pool_1024_api_url_template,
            num=num,
            scheme=self.settings.proxy_pool_scheme,
        )

    def _plan(self, proxies: list[str]) -> list[tuple[int, str, list[str]]]:
        size = self._chunk_size()
        prefix = self._prefix()
        plan: list[tuple[int, str, list[str]]] = []
        for offset in range(0, len(proxies), size):
            index = offset // size + 1
            plan.append((index, f"{prefix}-{index:02d}", proxies[offset : offset + size]))
        return plan

    def _prefix(self) -> str:
        return (self.settings.proxy_pool_subscription_prefix or "").strip() or DEFAULT_PREFIX

    def _platform_prefix(self) -> str:
        return (
            self.settings.proxy_pool_platform_prefix or ""
        ).strip() or DEFAULT_PLATFORM_PREFIX

    def _platform_name(self, index: int) -> str:
        return f"{self._platform_prefix()}-{index:02d}"

    def _resolve_target(self, override: int | None) -> int:
        if override is not None:
            value = int(override)
        else:
            value = int(self.settings.proxy_pool_target_ip_count)
        if value <= 0:
            raise ValueError("提取 IP 总数必须大于 0")
        if value > 100_000:
            raise ValueError("提取 IP 总数不能超过 100000")
        return value

    @staticmethod
    def _public_group(
        group: dict[str, Any], egress_enabled: bool | None = None
    ) -> dict[str, Any]:
        proxies = [str(value) for value in group.get("proxies") or []]
        return {
            "egressEnabled": egress_enabled,
            "id": group.get("id"),
            "index": group.get("group_index") or 0,
            "name": group.get("name") or "",
            "subscriptionId": group.get("subscription_id") or "",
            "platformName": group.get("platform_name") or "",
            "egressNodeId": group.get("egress_node_id"),
            "egressNodeName": group.get("egress_node_name") or "",
            "size": group.get("size") or 0,
            "scheme": group.get("scheme") or "",
            "status": group.get("status") or "pending",
            "lastError": group.get("last_error") or "",
            "lastRefreshedAt": group.get("last_refreshed_at"),
            "leaseExpiresAt": group.get("lease_expires_at"),
            "proxies": proxies,
            "sample": proxies[:3],
        }

    async def _worker(self) -> None:
        while True:
            try:
                await self._refresh_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the loop alive across transient failures
                logger.exception("proxy pool auto refresh failed")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=SCAN_INTERVAL_SECONDS
                )
            except TimeoutError:
                pass
            self._wake.clear()

    async def _refresh_due(self) -> None:
        if not self.settings.proxy_pool_auto_refresh_enabled:
            return
        if self._mode() == "url" and not self.settings.proxy_pool_1024_api_url_template:
            return
        due = self.repository.due_groups(before=utc_now())
        if not due:
            return
        logger.info("proxy pool refreshing %s expired groups", len(due))
        await self.import_groups()
