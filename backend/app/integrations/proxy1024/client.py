from __future__ import annotations

import json
import re
import secrets
import string
from typing import Any
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession as CurlAsyncSession

REQUEST_TIMEOUT_SECONDS = 30
NUM_PLACEHOLDER = "{num}"
_HOST_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-]*$")
_SID_ALPHABET = string.ascii_letters + string.digits
_GATEWAY_HOST_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-]*$")


class Proxy1024Error(RuntimeError):
    """Raised when the 1024proxy extraction API cannot return a proxy list."""


def build_extraction_url(template: str, *, num: int) -> str:
    value = (template or "").strip()
    if not value:
        raise Proxy1024Error("尚未配置 1024proxy 提取链接模板")
    if NUM_PLACEHOLDER not in value:
        raise Proxy1024Error("1024proxy 提取链接模板必须包含 {num} 占位符")
    if num <= 0:
        raise Proxy1024Error("提取 IP 数量必须大于 0")
    return value.replace(NUM_PLACEHOLDER, str(int(num)))


async def fetch_proxies(
    template: str,
    *,
    num: int,
    scheme: str = "socks5",
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[str]:
    """Call the 1024proxy extraction API and normalize the returned proxies."""

    url = build_extraction_url(template, num=num)
    try:
        async with CurlAsyncSession(impersonate="chrome") as client:
            response = await client.get(url, timeout=timeout)
    except Exception as exc:  # curl_cffi raises its own error hierarchy
        raise Proxy1024Error(f"1024proxy 提取请求失败: {exc}") from exc
    if response.status_code >= 300:
        detail = response.text[:500]
        raise Proxy1024Error(
            f"1024proxy 返回 HTTP {response.status_code}: {detail or '空响应'}"
        )
    payload = _decode_payload(response)
    proxies = parse_proxy_entries(payload, scheme=scheme)
    if not proxies:
        raise Proxy1024Error("1024proxy 未返回任何可用代理，请检查提取链接与套餐")
    return proxies


def _decode_payload(response: Any) -> Any:
    text = response.text or ""
    stripped = text.strip()
    if not stripped:
        return []
    content_type = str(response.headers.get("Content-Type") or "").lower()
    looks_json = "json" in content_type or stripped[0] in "[{"
    if not looks_json:
        return stripped
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return stripped


def generate_gateway_proxies(
    *,
    count: int,
    host: str,
    port: int,
    username: str,
    password: str,
    region: str,
    sticky: str = "1",
    scheme: str = "socks5",
) -> list[str]:
    """Generate sticky-session gateway lines with a unique random sid per node.

    The 1024proxy account gateway pins a residential exit IP per ``sid`` for
    the configured sticky duration, so each generated line behaves like a
    long-lived proxy node once an upstream pool resolves it.
    """

    normalized_host = (host or "").strip()
    normalized_user = (username or "").strip()
    normalized_password = password or ""
    normalized_region = (region or "").strip()
    normalized_sticky = (sticky or "").strip() or "1"
    normalized_scheme = (scheme or "socks5").strip().lower() or "socks5"
    if count <= 0:
        raise Proxy1024Error("生成的节点数量必须大于 0")
    if not normalized_host or not _GATEWAY_HOST_PATTERN.match(normalized_host):
        raise Proxy1024Error("网关地址无效")
    if not (1 <= int(port) <= 65535):
        raise Proxy1024Error("网关端口无效")
    if not normalized_user or not normalized_password:
        raise Proxy1024Error("网关账号或密码不能为空")
    if not normalized_region:
        raise Proxy1024Error("网关地区不能为空")

    lines: list[str] = []
    seen: set[str] = set()
    while len(lines) < count:
        sid = "".join(secrets.choice(_SID_ALPHABET) for _ in range(10))
        if sid in seen:
            continue
        seen.add(sid)
        gateway_user = (
            f"{normalized_user}-region-{normalized_region}"
            f"-sid-{sid}-t-{normalized_sticky}"
        )
        lines.append(
            f"{normalized_scheme}://{gateway_user}:{normalized_password}"
            f"@{normalized_host}:{int(port)}"
        )
    return lines


def parse_proxy_entries(payload: Any, *, scheme: str = "socks5") -> list[str]:
    """Normalize any 1024proxy response shape into Resin proxy lines."""

    normalized_scheme = (scheme or "socks5").strip().lower() or "socks5"
    result: list[str] = []
    seen: set[str] = set()
    for raw in _extract_items(payload):
        entry = normalize_proxy_entry(raw, scheme=normalized_scheme)
        if entry and entry not in seen:
            seen.add(entry)
            result.append(entry)
    return result


def _extract_items(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, (list, tuple, set)):
        items: list[str] = []
        for value in payload:
            text = str(value).strip()
            if text:
                items.append(text)
        return items
    if isinstance(payload, dict):
        for key in ("data", "list", "result", "proxies", "ips", "items", "rows"):
            if key in payload:
                nested = _extract_items(payload[key])
                if nested:
                    return nested
        for value in payload.values():
            if isinstance(value, (list, tuple)):
                nested = _extract_items(value)
                if nested:
                    return nested
        return []
    if isinstance(payload, str):
        return [
            line.strip()
            for line in re.split(r"[\r\n,;]+", payload)
            if line.strip()
        ]
    return []


def normalize_proxy_entry(entry: str, *, scheme: str = "socks5") -> str:
    """Convert one 1024proxy line into ``scheme://[user:pass@]host:port``."""

    value = str(entry or "").strip()
    if not value:
        return ""
    value = value.split("#", 1)[0].strip()
    if not value:
        return ""
    if "://" in value:
        return _normalize_url_entry(value, fallback_scheme=scheme)
    if "@" in value:
        left, right = value.split("@", 1)
        hostport = _as_hostport(left)
        credentials = right
        if hostport is None:
            hostport = _as_hostport(right)
            credentials = left
        if hostport is None:
            return ""
        return _compose(scheme, hostport, credentials)
    parts = value.split(":")
    if len(parts) == 2:
        return _compose(scheme, _as_hostport(value), "")
    if len(parts) == 4:
        credentials = f"{parts[2]}:{parts[3]}"
        return _compose(scheme, _as_hostport(f"{parts[0]}:{parts[1]}"), credentials)
    return ""


def _normalize_url_entry(value: str, *, fallback_scheme: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if not host or not port:
        return ""
    scheme = (parsed.scheme or fallback_scheme).lower() or fallback_scheme
    credentials = ""
    if parsed.username:
        credentials = f"{parsed.username}:{parsed.password or ''}"
    return _compose(scheme, (host, str(port)), credentials)


def _compose(scheme: str, hostport: tuple[str, str] | None, credentials: str) -> str:
    if hostport is None:
        return ""
    host, port = hostport
    auth = ""
    if credentials:
        user, _, password = credentials.partition(":")
        user = user.strip()
        if user:
            auth = f"{user}:{password.strip()}@"
    return f"{scheme}://{auth}{host}:{port}"


def _as_hostport(value: str) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        return None
    if not _HOST_PATTERN.match(host):
        return None
    number = int(port)
    if number <= 0 or number > 65535:
        return None
    return host, str(number)
