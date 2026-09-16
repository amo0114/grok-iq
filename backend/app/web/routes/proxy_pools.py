from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from app.services.proxy_pool_service import ProxyPoolService
from app.services.settings_service import RuntimeSettingsService
from app.web.schemas import (
    ProxyPoolConfigInput,
    ProxyPoolDeleteInput,
    ProxyPoolEgressInput,
    ProxyPoolImportInput,
)

from ._shared import disable_client_cache

CONFIG_FIELD_MAP = {
    "api_url_template": "proxy_pool_1024_api_url_template",
    "resin_base_url": "proxy_pool_resin_base_url",
    "resin_admin_token": "proxy_pool_resin_admin_token",
    "group_size": "proxy_pool_group_size",
    "target_ip_count": "proxy_pool_target_ip_count",
    "lease_hours": "proxy_pool_lease_hours",
    "scheme": "proxy_pool_scheme",
    "subscription_prefix": "proxy_pool_subscription_prefix",
    "auto_refresh_enabled": "proxy_pool_auto_refresh_enabled",
    "mode": "proxy_pool_mode",
    "gateway_host": "proxy_pool_gateway_host",
    "gateway_port": "proxy_pool_gateway_port",
    "gateway_username": "proxy_pool_gateway_username",
    "gateway_password": "proxy_pool_gateway_password",
    "gateway_region": "proxy_pool_gateway_region",
    "gateway_sticky": "proxy_pool_gateway_sticky",
    "over_factor": "proxy_pool_over_factor",
    "platform_prefix": "proxy_pool_platform_prefix",
    "auto_egress": "proxy_pool_auto_egress",
    "egress_capacity_factor": "proxy_pool_egress_capacity_factor",
    "resin_proxy_token": "proxy_pool_resin_proxy_token",
}


def build_proxy_pools_router(
    service: ProxyPoolService,
    runtime_settings: RuntimeSettingsService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/proxy-pool/config")
    def get_proxy_pool_config(response: Response) -> dict[str, Any]:
        disable_client_cache(response)
        return runtime_settings.proxy_pool_view()

    @router.put("/proxy-pool/config")
    async def update_proxy_pool_config(
        payload: ProxyPoolConfigInput,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field, setting in CONFIG_FIELD_MAP.items():
            value = getattr(payload, field)
            if value is not None:
                values[setting] = value
        changed = runtime_settings.update(values)
        service.wake()
        return {
            "changed": changed,
            "config": runtime_settings.proxy_pool_view(),
        }

    @router.get("/proxy-pool/groups")
    async def list_proxy_pool_groups() -> dict[str, Any]:
        return await service.list_groups()

    @router.post("/proxy-pool/preview")
    async def preview_proxy_pool(
        payload: ProxyPoolImportInput | None = None,
    ) -> dict[str, Any]:
        total = payload.total if payload is not None else None
        return await service.preview(total=total)

    @router.post("/proxy-pool/import")
    async def import_proxy_pool(
        payload: ProxyPoolImportInput | None = None,
    ) -> dict[str, Any]:
        total = payload.total if payload is not None else None
        result = await service.import_groups(total=total)
        service.wake()
        return result

    @router.post("/proxy-pool/refresh")
    async def refresh_proxy_pool() -> dict[str, Any]:
        result = await service.refresh_all()
        service.wake()
        return result

    @router.post("/proxy-pool/groups/{group_id}/refresh")
    async def refresh_proxy_pool_group(group_id: int) -> dict[str, Any]:
        return await service.refresh_group(group_id)

    @router.post("/proxy-pool/groups/{group_id}/egress")
    async def set_proxy_pool_group_egress(
        group_id: int,
        payload: ProxyPoolEgressInput,
    ) -> dict[str, Any]:
        return await service.set_group_egress(group_id, enabled=payload.enabled)

    @router.post("/proxy-pool/sync-egress")
    async def sync_proxy_pool_egress() -> dict[str, Any]:
        return await service.sync_egress_now()

    @router.delete("/proxy-pool/groups")
    async def delete_proxy_pool_groups(
        payload: ProxyPoolDeleteInput,
    ) -> dict[str, Any]:
        return await service.delete_groups(group_ids=payload.ids)

    return router
