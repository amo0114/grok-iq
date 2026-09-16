from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app.core.clock import utc_now
from app.core.config import Settings
from app.persistence.database import Database
from app.persistence.proxy_pool_repository import ProxyPoolRepository
from app.services import proxy_pool_service as proxy_pool_module
from app.services.proxy_pool_service import ProxyPoolService


class FakeResin:
    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []
        self.updated: list[dict[str, str | None]] = []
        self.deleted: list[str] = []
        self._seq = 0

    async def create_subscription(
        self,
        *,
        name: str,
        content: str,
        update_interval: str = "24h",
        enabled: bool = True,
        incremental_alive_nodes: bool = False,
    ) -> dict[str, str]:
        self._seq += 1
        sub_id = f"sub-{self._seq}"
        self.created.append({"id": sub_id, "name": name, "content": content})
        return {"id": sub_id, "name": name}

    async def update_subscription(
        self,
        subscription_id: str,
        *,
        content: str | None = None,
        name: str | None = None,
        update_interval: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, str]:
        self.updated.append({"id": subscription_id, "content": content})
        return {"id": subscription_id}

    async def delete_subscription(self, subscription_id: str) -> None:
        self.deleted.append(subscription_id)


def build_service(tmp_path: Path, resin: FakeResin):
    database = Database(tmp_path / "grokiq.db")
    database.initialize()
    settings = Settings(_env_file=None)
    settings.proxy_pool_1024_api_url_template = "https://api.test/extract?num={num}"
    settings.proxy_pool_group_size = 50
    settings.proxy_pool_target_ip_count = 100
    settings.proxy_pool_lease_hours = 24
    settings.proxy_pool_scheme = "socks5"
    settings.proxy_pool_subscription_prefix = "grokiq-1024"
    repository = ProxyPoolRepository(database)
    service = ProxyPoolService(settings=settings, repository=repository, resin=resin)
    return settings, repository, service


def patch_fetch(monkeypatch, calls: list[int]):
    async def fake_fetch(template, *, num, scheme="socks5", timeout=30):
        calls.append(num)
        return [f"socks5://u:p@10.0.0.{index + 1}:8080" for index in range(num)]

    monkeypatch.setattr(proxy_pool_module, "fetch_proxies", fake_fetch)


async def test_import_creates_one_subscription_per_group(tmp_path, monkeypatch):
    calls: list[int] = []
    patch_fetch(monkeypatch, calls)
    resin = FakeResin()
    _, repository, service = build_service(tmp_path, resin)

    result = await service.import_groups()

    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["fetched"] == 100
    assert calls == [100]
    assert [item["name"] for item in resin.created] == [
        "grokiq-1024-01",
        "grokiq-1024-02",
    ]
    groups = repository.list_groups()
    assert [group["name"] for group in groups] == ["grokiq-1024-01", "grokiq-1024-02"]
    assert groups[0]["subscription_id"] == "sub-1"
    assert len(groups[0]["proxies"]) == 50
    assert groups[0]["proxies"][0] == "socks5://u:p@10.0.0.1:8080"
    assert groups[1]["proxies"][0] == "socks5://u:p@10.0.0.51:8080"
    assert groups[0]["lease_expires_at"] > utc_now()


async def test_reimport_updates_existing_subscriptions(tmp_path, monkeypatch):
    calls: list[int] = []
    patch_fetch(monkeypatch, calls)
    resin = FakeResin()
    _, repository, service = build_service(tmp_path, resin)

    await service.import_groups()
    result = await service.import_groups()

    assert result["created"] == 0
    assert result["updated"] == 2
    assert calls == [100, 100]
    assert len(resin.created) == 2
    assert [item["id"] for item in resin.updated] == ["sub-1", "sub-2"]
    assert len(repository.list_groups()) == 2


async def test_refresh_group_replaces_content(tmp_path, monkeypatch):
    calls: list[int] = []
    patch_fetch(monkeypatch, calls)
    resin = FakeResin()
    _, repository, service = build_service(tmp_path, resin)

    await service.import_groups()
    group_id = int(repository.list_groups()[0]["id"])
    result = await service.refresh_group(group_id)

    assert result["action"] == "updated"
    assert calls[-1] == 50
    assert resin.updated[-1]["id"] == "sub-1"


async def test_delete_groups_removes_remote_and_local(tmp_path, monkeypatch):
    calls: list[int] = []
    patch_fetch(monkeypatch, calls)
    resin = FakeResin()
    _, repository, service = build_service(tmp_path, resin)

    await service.import_groups()
    group_ids = [int(group["id"]) for group in repository.list_groups()]
    result = await service.delete_groups(group_ids=group_ids)

    assert result["deleted"] == 2
    assert result["removedRemote"] == 2
    assert sorted(resin.deleted) == ["sub-1", "sub-2"]
    assert repository.list_groups() == []


async def test_gateway_mode_generates_sid_nodes(tmp_path):
    database = Database(tmp_path / "grokiq.db")
    database.initialize()
    settings = Settings(_env_file=None)
    settings.proxy_pool_mode = "gateway"
    settings.proxy_pool_group_size = 50
    settings.proxy_pool_target_ip_count = 50
    settings.proxy_pool_over_factor = 2
    settings.proxy_pool_gateway_host = "us.1024proxy.io"
    settings.proxy_pool_gateway_port = 3000
    settings.proxy_pool_gateway_username = "tlrp743120"
    settings.proxy_pool_gateway_password = "secret"
    settings.proxy_pool_gateway_region = "SG"
    settings.proxy_pool_subscription_prefix = "grokiq-1024"
    repository = ProxyPoolRepository(database)
    resin = FakeResin()
    service = ProxyPoolService(settings=settings, repository=repository, resin=resin)

    result = await service.import_groups()

    assert result["mode"] == "gateway"
    assert result["fetched"] == 100
    assert result["groupCount"] == 1
    groups = repository.list_groups()
    assert len(groups) == 1
    assert groups[0]["size"] == 100
    assert all("-region-SG-sid-" in proxy for proxy in groups[0]["proxies"])


async def test_refresh_due_only_runs_when_enabled_and_expired(tmp_path, monkeypatch):
    calls: list[int] = []
    patch_fetch(monkeypatch, calls)
    resin = FakeResin()
    settings, repository, service = build_service(tmp_path, resin)

    await service.import_groups()
    assert calls == [100]

    await service._refresh_due()
    assert calls == [100]  # auto refresh disabled by default

    settings.proxy_pool_auto_refresh_enabled = True
    await service._refresh_due()
    assert calls == [100]  # not expired yet

    existing = repository.list_groups()[0]
    repository.upsert_group(
        group_index=existing["group_index"],
        name=existing["name"],
        subscription_id=existing["subscription_id"],
        size=existing["size"],
        scheme=existing["scheme"],
        proxies=existing["proxies"],
        status=existing["status"],
        error="",
        lease_expires_at=utc_now() - timedelta(hours=1),
    )
    await service._refresh_due()
    assert calls[-1] == 100
    assert len(resin.updated) >= 2
