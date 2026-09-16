from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from app.core.clock import utc_now
from app.core.config import Settings
from app.integrations.proxy1024 import Proxy1024Error, fetch_proxies
from app.integrations.resin import ResinClient, ResinError
from app.persistence.proxy_pool_repository import STATUS_ACTIVE, ProxyPoolRepository

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60.0
DEFAULT_PREFIX = "grokiq-1024"


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
    ):
        self.settings = settings
        self.repository = repository
        self.resin = resin or ResinClient(settings)
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

    def list_groups(self) -> dict[str, Any]:
        groups = [self._public_group(group) for group in self.repository.list_groups()]
        return {
            "groups": groups,
            "total": len(groups),
            "groupSize": self.settings.proxy_pool_group_size,
            "leaseHours": self.settings.proxy_pool_lease_hours,
            "prefix": self._prefix(),
        }

    async def preview(self, *, total: int | None = None) -> dict[str, Any]:
        count = self._resolve_target(total)
        proxies = await self._fetch(count)
        plan = self._plan(proxies)
        return {
            "requested": count,
            "fetched": len(proxies),
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
        proxies = await self._fetch(count)
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
        size = max(1, int(group.get("size") or self.settings.proxy_pool_group_size))
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
        for group_id in dict.fromkeys(int(value) for value in group_ids):
            group = groups.get(group_id)
            if group is None:
                continue
            subscription_id = str(group.get("subscription_id") or "")
            if not subscription_id:
                continue
            try:
                await self.resin.delete_subscription(subscription_id)
                removed_remote += 1
            except ResinError as exc:
                if not exc.not_found:
                    remote_errors.append(
                        {"name": group.get("name"), "error": str(exc)}
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
        )
        return record, action

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

    async def _fetch(self, num: int) -> list[str]:
        return await fetch_proxies(
            self.settings.proxy_pool_1024_api_url_template,
            num=num,
            scheme=self.settings.proxy_pool_scheme,
        )

    def _plan(self, proxies: list[str]) -> list[tuple[int, str, list[str]]]:
        size = self.settings.proxy_pool_group_size
        prefix = self._prefix()
        plan: list[tuple[int, str, list[str]]] = []
        for offset in range(0, len(proxies), size):
            index = offset // size + 1
            plan.append((index, f"{prefix}-{index:02d}", proxies[offset : offset + size]))
        return plan

    def _prefix(self) -> str:
        return (self.settings.proxy_pool_subscription_prefix or "").strip() or DEFAULT_PREFIX

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
    def _public_group(group: dict[str, Any]) -> dict[str, Any]:
        proxies = [str(value) for value in group.get("proxies") or []]
        return {
            "id": group.get("id"),
            "index": group.get("group_index") or 0,
            "name": group.get("name") or "",
            "subscriptionId": group.get("subscription_id") or "",
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
        if not self.settings.proxy_pool_1024_api_url_template:
            return
        due = self.repository.due_groups(before=utc_now())
        if not due:
            return
        logger.info("proxy pool refreshing %s expired groups", len(due))
        await self.import_groups()
