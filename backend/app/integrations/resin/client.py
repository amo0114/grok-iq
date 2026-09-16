from __future__ import annotations

import json
from typing import Any

from curl_cffi.requests import AsyncSession as CurlAsyncSession

from app.core.config import Settings

REQUEST_TIMEOUT_SECONDS = 30


class ResinError(RuntimeError):
    """Raised when the Resin admin API rejects a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: str = "",
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body

    @property
    def not_found(self) -> bool:
        return self.status_code == 404


class ResinClient:
    """Minimal Resin admin API client for subscription management."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def base_url(self) -> str:
        return (self.settings.proxy_pool_resin_base_url or "").rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = (self.settings.proxy_pool_resin_admin_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _require_base_url(self) -> str:
        url = self.base_url
        if not url:
            raise ResinError("尚未配置 Resin 地址")
        return url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        base = self._require_base_url()
        try:
            async with CurlAsyncSession(impersonate="chrome") as client:
                response = await client.request(
                    method,
                    f"{base}{path}",
                    headers=self._headers(),
                    json=json_body,
                    params=params,
                    timeout=timeout,
                )
        except Exception as exc:
            raise ResinError(f"Resin 请求失败: {exc}") from exc
        if response.status_code >= 300:
            code, message = _parse_error(response.text or "")
            raise ResinError(
                message or f"Resin 返回 HTTP {response.status_code}",
                status_code=response.status_code,
                code=code,
                body=(response.text or "")[:2000],
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise ResinError("Resin 返回了无法解析的响应") from exc

    async def list_subscriptions(self, *, keyword: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"page": 1, "pageSize": 1000}
        if keyword:
            params["keyword"] = keyword
        payload = await self._request("GET", "/api/v1/subscriptions", params=params)
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET", f"/api/v1/subscriptions/{subscription_id}"
        )
        return payload if isinstance(payload, dict) else {}

    async def create_subscription(
        self,
        *,
        name: str,
        content: str,
        update_interval: str = "24h",
        enabled: bool = True,
        incremental_alive_nodes: bool = False,
    ) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/api/v1/subscriptions",
            json_body={
                "name": name,
                "source_type": "local",
                "content": content,
                "update_interval": update_interval,
                "enabled": enabled,
                "incremental_alive_nodes": incremental_alive_nodes,
            },
        )
        return payload if isinstance(payload, dict) else {}

    async def update_subscription(
        self,
        subscription_id: str,
        *,
        content: str | None = None,
        name: str | None = None,
        update_interval: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if content is not None:
            body["content"] = content
        if name is not None:
            body["name"] = name
        if update_interval is not None:
            body["update_interval"] = update_interval
        if enabled is not None:
            body["enabled"] = enabled
        if not body:
            return await self.get_subscription(subscription_id)
        payload = await self._request(
            "PATCH",
            f"/api/v1/subscriptions/{subscription_id}",
            json_body=body,
        )
        return payload if isinstance(payload, dict) else {}

    async def delete_subscription(self, subscription_id: str) -> None:
        await self._request("DELETE", f"/api/v1/subscriptions/{subscription_id}")


def _parse_error(body: str) -> tuple[str, str]:
    text = (body or "").strip()
    if not text:
        return "", ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return "", text[:300]
    if not isinstance(payload, dict):
        return "", text[:300]
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or ""), str(error.get("message") or "")
    return str(payload.get("code") or ""), str(payload.get("message") or "")
