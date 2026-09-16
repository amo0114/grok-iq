from __future__ import annotations

import pytest

from app.integrations.proxy1024.client import (
    Proxy1024Error,
    build_extraction_url,
    parse_proxy_entries,
)


def test_normalize_supported_proxy_formats():
    entries = [
        "1.2.3.4:8080:user:pass",
        "user:pass@5.6.7.8:9090",
        "9.9.9.9:1080@alice:secret",
        "socks5://bob:pw@10.0.0.1:1080",
        "http://10.0.0.2:3128",
        "11.0.0.1:8888",
    ]

    assert parse_proxy_entries(entries, scheme="socks5") == [
        "socks5://user:pass@1.2.3.4:8080",
        "socks5://user:pass@5.6.7.8:9090",
        "socks5://alice:secret@9.9.9.9:1080",
        "socks5://bob:pw@10.0.0.1:1080",
        "http://10.0.0.2:3128",
        "socks5://11.0.0.1:8888",
    ]


def test_parse_handles_json_envelopes_and_deduplicates():
    payload = {
        "code": 0,
        "data": {
            "list": [
                "1.2.3.4:8080:user:pass",
                "1.2.3.4:8080:user:pass",
                "5.6.7.8:9090:user:pass",
            ]
        },
    }

    assert parse_proxy_entries(payload, scheme="socks5") == [
        "socks5://user:pass@1.2.3.4:8080",
        "socks5://user:pass@5.6.7.8:9090",
    ]


def test_parse_splits_text_lists():
    text = "1.2.3.4:8080:u:p\n5.6.7.8:8080:u:p,9.9.9.9:8080:u:p"

    assert parse_proxy_entries(text, scheme="socks5") == [
        "socks5://u:p@1.2.3.4:8080",
        "socks5://u:p@5.6.7.8:8080",
        "socks5://u:p@9.9.9.9:8080",
    ]


def test_parse_drops_invalid_entries():
    assert parse_proxy_entries(["not a proxy", "1.2.3.4", ":8080:u:p"]) == []
    assert parse_proxy_entries(["1.2.3.4:99999:u:p"]) == []


def test_build_extraction_url_requires_num_placeholder():
    assert (
        build_extraction_url("https://api.test/extract?num={num}&t=1", num=50)
        == "https://api.test/extract?num=50&t=1"
    )
    with pytest.raises(Proxy1024Error):
        build_extraction_url("https://api.test/extract", num=50)
    with pytest.raises(Proxy1024Error):
        build_extraction_url("", num=50)
