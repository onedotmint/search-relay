import json
import pathlib
from datetime import datetime, timezone
from dataclasses import replace

import httpx

from app.providers import PROVIDERS, AuthStrategy, RouteConfig
from app.repositories import (
    create_provider,
    create_relay_key,
    list_provider_api_keys,
    list_search_cache_entries,
    recent_request_logs,
    set_cache_settings,
)
from app.security import hash_secret


def add_relay_key(client, raw_key="relay_test_key", daily_limit=None):
    key_id = create_relay_key(client.app.state.db, "test", hash_secret(raw_key), daily_limit)
    return key_id, raw_key


def test_no_retrieval_intelligence_in_relay():
    """Search Relay must not implement retrieval intelligence (prd.md boundary).

    Smart Search owns fusion / RRF / query routing / DiscoveryCandidate; the
    relay core (relay + repositories) must stay free of those concepts so the
    provider-access boundary holds. "rerank" is intentionally not asserted —
    it is a legitimate Jina route name in the provider registry.
    """
    forbidden = {"DiscoveryCandidate", "reciprocal", "RRF", "fusion", "query_router"}
    project_root = pathlib.Path(__file__).resolve().parents[1]
    sources = [
        (project_root / "app" / "relay.py").read_text(),
        (project_root / "app" / "repositories.py").read_text(),
    ]
    for source in sources:
        for token in forbidden:
            assert token not in source, (
                f"retrieval-intelligence token {token!r} must not appear in relay core"
            )


def test_missing_relay_key_is_rejected(client):
    response = client.post("/exa/search", json={"query": "ai"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "relay_auth_failed"


def test_provider_must_be_enabled(client):
    _, raw_key = add_relay_key(client)

    response = client.post("/exa/search", headers={"Authorization": f"Bearer {raw_key}"}, json={"query": "ai"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_relay_forwards_to_mocked_exa(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    captured = {}

    async def fake_post(self, url, content, headers):
        captured["url"] = url
        captured["headers"] = headers
        captured["content"] = content
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}
    assert captured["url"] == "https://api.exa.ai/search"
    assert captured["headers"]["x-api-key"] == "exa-secret"
    logs = recent_request_logs(client.app.state.db)
    assert logs[0]["provider"] == "exa"
    assert logs[0]["status_code"] == 200


def test_relay_round_robins_enabled_provider_keys(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "primary", "api_key": "exa-primary-secret"},
    ).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "backup", "api_key": "exa-backup-secret"},
    ).status_code == 200
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    for _ in range(2):
        response = client.post(
            "/exa/search?no_cache=true",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"query": "ai"},
        )
        assert response.status_code == 200

    assert used_keys == ["exa-primary-secret", "exa-backup-secret"]


def test_daily_limit_returns_429(client):
    _, raw_key = add_relay_key(client, daily_limit=0)
    create_provider(client.app.state.db, "tavily", "tvly-secret", True)

    response = client.post(
        "/tavily/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "daily_limit_exceeded"


def test_disabled_group_blocks_external_key(client):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    group_response = client.post("/api/admin/groups", json={"name": "blocked", "platform": "exa", "enabled": True})
    group = group_response.json()["group"]
    key_response = client.post(
        "/api/admin/relay-keys",
        json={"label": "blocked-client", "exa_group_id": group["id"], "daily_limit": None},
    )
    raw_key = key_response.json()["relay_key"]
    assert client.post(f"/api/admin/groups/{group['id']}/disable").status_code == 200

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "group_disabled"


def test_unassigned_provider_group_returns_403(client):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    group_response = client.post("/api/admin/groups", json={"name": "exa-only", "platform": "exa", "enabled": True})
    group = group_response.json()["group"]
    key_response = client.post(
        "/api/admin/relay-keys",
        json={"label": "exa-only-client", "exa_group_id": group["id"], "tavily_group_id": None, "daily_limit": None},
    )
    raw_key = key_response.json()["relay_key"]

    response = client.post(
        "/tavily/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provider_group_unassigned"


def test_relay_uses_provider_key_from_external_key_group(client, monkeypatch):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    vip_group = client.post(
        "/api/admin/groups",
        json={"name": "exa-vip", "platform": "exa", "enabled": True},
    ).json()["group"]
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "default-pool", "api_key": "exa-default-secret"},
    ).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "vip-pool", "api_key": "exa-vip-secret", "group_id": vip_group["id"]},
    ).status_code == 200
    key_response = client.post(
        "/api/admin/relay-keys",
        json={"label": "vip-client", "exa_group_id": vip_group["id"], "daily_limit": None},
    )
    raw_key = key_response.json()["relay_key"]
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert used_keys == ["exa-vip-secret"]


def test_relay_load_balances_across_bound_groups(client, monkeypatch):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    first_group = client.post("/api/admin/groups", json={"name": "exa-lb-a", "platform": "exa", "enabled": True}).json()["group"]
    second_group = client.post("/api/admin/groups", json={"name": "exa-lb-b", "platform": "exa", "enabled": True}).json()["group"]
    assert client.post("/api/admin/providers/exa/keys", json={"label": "a", "api_key": "exa-a", "group_id": first_group["id"]}).status_code == 200
    assert client.post("/api/admin/providers/exa/keys", json={"label": "b", "api_key": "exa-b", "group_id": second_group["id"]}).status_code == 200
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "lb-client", "exa_group_ids": [first_group["id"], second_group["id"]], "daily_limit": None},
    ).json()["relay_key"]
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    for index in range(2):
        response = client.post(
            f"/exa/search?no_cache=true&index={index}",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"query": "ai"},
        )
        assert response.status_code == 200

    assert used_keys == ["exa-a", "exa-b"]
    logs = recent_request_logs(client.app.state.db, limit=2)
    assert {log["provider_group_name"] for log in logs} == {"exa-lb-a", "exa-lb-b"}


def test_relay_uses_selected_group_socks5_proxy(client, monkeypatch):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": "exa-proxy", "platform": "exa", "enabled": True, "socks5_proxy": "socks5://127.0.0.1:1080"},
    ).json()["group"]
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "proxy-key", "api_key": "exa-proxy-key", "group_id": group["id"]},
    ).status_code == 200
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "proxy-client", "exa_group_ids": [group["id"]], "daily_limit": None},
    ).json()["relay_key"]
    captured_clients = []
    original_init = httpx.AsyncClient.__init__

    def fake_init(self, *args, **kwargs):
        captured_clients.append(kwargs.get("proxy"))
        kwargs.pop("proxy", None)
        original_init(self, *args, **kwargs)

    async def fake_post(self, url, content, headers):
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search?no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert captured_clients == ["socks5://127.0.0.1:1080"]


def test_relay_falls_back_to_next_group_after_retryable_failure(client, monkeypatch):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    first_group = client.post("/api/admin/groups", json={"name": "exa-fail-a", "platform": "exa", "enabled": True}).json()["group"]
    second_group = client.post("/api/admin/groups", json={"name": "exa-fail-b", "platform": "exa", "enabled": True}).json()["group"]
    assert client.post("/api/admin/providers/exa/keys", json={"label": "a", "api_key": "exa-a", "group_id": first_group["id"]}).status_code == 200
    assert client.post("/api/admin/providers/exa/keys", json={"label": "b", "api_key": "exa-b", "group_id": second_group["id"]}).status_code == 200
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "fallback-client", "exa_group_ids": [first_group["id"], second_group["id"]], "daily_limit": None},
    ).json()["relay_key"]
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        if headers["x-api-key"] == "exa-a":
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"results": [{"title": "ok"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search?no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"title": "ok"}]}
    assert used_keys == ["exa-a", "exa-b"]


def test_relay_falls_back_when_provider_key_is_invalid(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "bad", "api_key": "exa-bad", "total_quota": 1000},
    ).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "good", "api_key": "exa-good", "total_quota": 900},
    ).status_code == 200
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        if headers["x-api-key"] == "exa-bad":
            return httpx.Response(401, json={"error": "invalid api key"})
        return httpx.Response(200, json={"results": [{"title": "ok"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"title": "ok"}]}
    assert used_keys == ["exa-bad", "exa-good"]
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    bad = next(key for key in exa["upstream_keys"] if key["label"] == "bad")
    assert bad["is_invalid"] is True
    assert bad["enabled"] is False


def test_relay_all_rate_limited_keys_returns_429_without_invalidating_keys(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post("/api/admin/providers/exa/keys", json={"label": "a", "api_key": "exa-a"}).status_code == 200
    assert client.post("/api/admin/providers/exa/keys", json={"label": "b", "api_key": "exa-b"}).status_code == 200

    async def fake_post(self, url, content, headers):
        return httpx.Response(429, json={"error": "rate limited"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 429
    assert response.json() == {"error": "rate limited"}
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    assert all(key["is_invalid"] is False for key in exa["upstream_keys"])


def test_relay_accepts_json_body_api_key_and_strips_it_before_upstream(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    captured = {}

    async def fake_post(self, url, content, headers):
        captured["content"] = content
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        json={"api_key": raw_key, "query": "ai"},
    )

    assert response.status_code == 200
    assert b"api_key" not in captured["content"]
    assert b"relay_test_key" not in captured["content"]
    assert b'"query":"ai"' in captured["content"]


def test_relay_accepts_query_api_key_and_strips_it_before_upstream(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "tavily", "tvly-secret", True)
    captured = {}

    async def fake_post(self, url, content, headers):
        captured["url"] = url
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        f"/tavily/search?api_key={raw_key}&topic=news",
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert "api_key" not in captured["url"]
    assert "relay_test_key" not in captured["url"]
    assert captured["url"].endswith("/search?topic=news")


def test_relay_search_cache_returns_cached_success(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"title": f"call-{calls}"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    for _ in range(2):
        response = client.post(
            "/exa/search",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"query": "cached"},
        )
        assert response.status_code == 200

    assert calls == 1
    assert response.json() == {"results": [{"title": "call-1"}]}
    assert response.headers["x-search-relay-cache"] == "hit"


def test_relay_search_cache_can_be_disabled(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    set_cache_settings(client.app.state.db, enabled=False, ttl_seconds=43200, max_rows=10000)
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"title": f"call-{calls}"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    first_response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "disabled-cache"},
    )
    second_response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "disabled-cache"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert calls == 2
    assert "x-search-relay-cache" not in second_response.headers
    assert list_search_cache_entries(client.app.state.db, limit=10, offset=0) == []


def test_relay_search_cache_uses_configured_ttl_and_max_rows(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    set_cache_settings(client.app.state.db, enabled=True, ttl_seconds=1, max_rows=1)
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"title": f"call-{calls}"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    first_response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "first"},
    )
    second_response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "second"},
    )
    entries = list_search_cache_entries(client.app.state.db, limit=10, offset=0)
    expires_at = datetime.fromisoformat(entries[0]["expires_at"])
    seconds_until_expiry = (expires_at - datetime.now(timezone.utc)).total_seconds()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(entries) == 1
    assert entries[0]["response_bytes"] > 0
    assert 0 < seconds_until_expiry <= 2


def test_relay_search_cache_is_scoped_to_provider_group(client, monkeypatch):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    group_with_key = client.post(
        "/api/admin/groups",
        json={"name": "cached-exa", "platform": "exa", "enabled": True},
    ).json()["group"]
    empty_group = client.post(
        "/api/admin/groups",
        json={"name": "empty-exa", "platform": "exa", "enabled": True},
    ).json()["group"]
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "cached-key", "api_key": "exa-cached-secret", "group_id": group_with_key["id"]},
    ).status_code == 200
    first_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "cached-client", "exa_group_id": group_with_key["id"], "daily_limit": None},
    ).json()["relay_key"]
    second_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "empty-client", "exa_group_id": empty_group["id"], "daily_limit": None},
    ).json()["relay_key"]
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"title": "cached"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    first_response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {first_key}"},
        json={"query": "cached"},
    )
    second_response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {second_key}"},
        json={"query": "cached"},
    )

    assert first_response.status_code == 200
    assert first_response.headers["x-search-relay-cache"] == "miss"
    assert second_response.status_code == 503
    assert second_response.json()["error"]["code"] == "provider_unavailable"
    assert calls == 1


def test_relay_search_cache_varies_by_forwarded_query(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "tavily", "tvly-secret", True)
    calls = []

    async def fake_post(self, url, content, headers):
        calls.append(url)
        return httpx.Response(200, json={"results": [{"title": f"call-{len(calls)}"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    first_response = client.post(
        "/tavily/search?topic=news",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "cached"},
    )
    second_response = client.post(
        "/tavily/search?topic=general",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "cached"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == {"results": [{"title": "call-1"}]}
    assert second_response.json() == {"results": [{"title": "call-2"}]}
    assert calls == [
        "https://api.tavily.com/search?topic=news",
        "https://api.tavily.com/search?topic=general",
    ]


def test_unsupported_method_rejected(client):
    _, raw_key = add_relay_key(client)

    response = client.get("/exa/search", headers={"Authorization": f"Bearer {raw_key}"})

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "unsupported_method"


def test_unknown_provider_rejected(client):
    _, raw_key = add_relay_key(client)

    response = client.post(
        "/nope/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unsupported_route"


def test_get_request_forwards_sanitized_query_params(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    get_route = RouteConfig(
        upstream_url="https://api.exa.ai/test-get",
        methods=frozenset({"GET"}),
        auth=AuthStrategy(kind="header", header_name="x-api-key"),
    )
    monkeypatch.setitem(
        PROVIDERS,
        "exa",
        replace(PROVIDERS["exa"], routes={**PROVIDERS["exa"].routes, "test-get": get_route}),
    )
    captured = {}

    async def fake_request(self, method, url, params, content, headers):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        captured["content"] = content
        captured["headers"] = headers
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(f"/exa/test-get?q=ai&api_key={raw_key}")

    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.exa.ai/test-get"
    assert captured["params"] == [("q", "ai")]
    assert captured["content"] is None
    assert captured["headers"]["x-api-key"] == "exa-secret"
    assert "api_key" not in [key for key, _ in captured["params"]]
    assert "relay_test_key" not in repr(captured["params"])


def test_relay_key_not_leaked_in_query_for_get(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    get_route = RouteConfig(
        upstream_url="https://api.exa.ai/test-get",
        methods=frozenset({"GET"}),
        auth=AuthStrategy(kind="header", header_name="x-api-key"),
    )
    monkeypatch.setitem(
        PROVIDERS,
        "exa",
        replace(PROVIDERS["exa"], routes={**PROVIDERS["exa"].routes, "test-get": get_route}),
    )
    captured = {}

    async def fake_request(self, method, url, params, content, headers):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(f"/exa/test-get?q=ai&topic=news&api_key={raw_key}")

    assert response.status_code == 200
    assert captured["params"] == [("q", "ai"), ("topic", "news")]
    assert raw_key not in captured["url"]
    assert raw_key not in repr(captured["params"])
    assert raw_key not in repr(captured["headers"])


def test_body_passthrough_preserves_exact_bytes_for_post(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    captured = {}

    async def fake_post(self, url, content, headers):
        captured["content"] = content
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        json={"api_key": raw_key, "query": {"nested": [1, 2, {"x": "y"}]}, "numResults": 10},
    )

    assert response.status_code == 200
    expected = json.dumps(
        {"query": {"nested": [1, 2, {"x": "y"}]}, "numResults": 10},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert captured["content"] == expected
    assert b"api_key" not in captured["content"]
    assert raw_key.encode() not in captured["content"]


def test_error_codes_are_distinct(client):
    _, raw_key = add_relay_key(client)
    codes = []

    # Missing relay key -> relay_auth_failed
    response = client.post("/exa/search", json={"query": "ai"})
    codes.append(response.json()["error"]["code"])
    assert response.status_code == 401

    # Key bound only to a tavily group -> provider_group_unassigned for /exa/*
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": "tavily-only-groups", "platform": "tavily", "enabled": True},
    ).json()["group"]
    other_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "tavily-only-client", "tavily_group_id": group["id"], "daily_limit": None},
    ).json()["relay_key"]
    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {other_key}"},
        json={"query": "ai"},
    )
    codes.append(response.json()["error"]["code"])
    assert response.status_code == 403

    # Unknown route -> unsupported_route
    response = client.post(
        "/exa/nope",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )
    codes.append(response.json()["error"]["code"])
    assert response.status_code == 404

    # Wrong method -> unsupported_method
    response = client.get("/exa/search", headers={"Authorization": f"Bearer {raw_key}"})
    codes.append(response.json()["error"]["code"])
    assert response.status_code == 405

    # Provider disabled -> provider_unavailable
    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )
    codes.append(response.json()["error"]["code"])
    assert response.status_code == 503

    assert len(codes) == len(set(codes))
    assert codes == [
        "relay_auth_failed",
        "provider_group_unassigned",
        "unsupported_route",
        "unsupported_method",
        "provider_unavailable",
    ]


def test_allowlisted_upstream_header_passthrough(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)

    async def fake_post(self, url, content, headers):
        return httpx.Response(
            200,
            json={"results": []},
            headers={
                "cache-control": "max-age=60",
                "etag": "\"abc123\"",
                "last-modified": "Wed, 01 Jan 2025 00:00:00 GMT",
                "x-ratelimit-limit": "100",
                "x-ratelimit-remaining": "99",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "max-age=60"
    assert response.headers["etag"] == "\"abc123\""
    assert response.headers["last-modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert response.headers["x-ratelimit-limit"] == "100"
    assert response.headers["x-ratelimit-remaining"] == "99"


def test_hop_by_hop_and_secret_headers_never_forwarded(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)

    async def fake_post(self, url, content, headers):
        return httpx.Response(
            200,
            json={"results": []},
            headers={
                "connection": "close",
                "keep-alive": "timeout=5",
                "transfer-encoding": "chunked",
                "set-cookie": "session=abc",
                "authorization": "Bearer upstream-secret",
                "www-authenticate": "Bearer",
                "x-api-key": "upstream-key-value",
                "x-subscription-token": "token-value",
                "x-upstream-secret": "shh",
                "cache-control": "max-age=60",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "max-age=60"
    for name in (
        "connection",
        "keep-alive",
        "transfer-encoding",
        "set-cookie",
        "authorization",
        "www-authenticate",
        "x-api-key",
        "x-subscription-token",
        "x-upstream-secret",
    ):
        assert name not in response.headers
        assert name.lower() not in {key.lower() for key in response.headers.keys()}


def test_route_cache_gate_uses_cacheable_flag(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"title": f"call-{calls}"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    for _ in range(2):
        response = client.post(
            "/exa/contents",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"urls": ["https://example.com"]},
        )
        assert response.status_code == 200

    assert calls == 2
    assert "x-search-relay-cache" not in response.headers
    assert list_search_cache_entries(client.app.state.db, limit=10, offset=0) == []


def _brave_relay_setup(client, secrets=("brave-primary-secret",), group_name="brave-vip"):
    """Admin-API setup for brave relay tests: enable the provider, create one
    brave group with the given upstream keys, and a relay key bound to that
    group. Returns (raw_relay_key, group_id).
    """
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/brave", json={"enabled": True}).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": group_name, "platform": "brave", "enabled": True},
    ).json()["group"]
    for index, secret in enumerate(secrets):
        assert client.post(
            "/api/admin/providers/brave/keys",
            json={"label": f"brave-key-{index}", "api_key": secret, "group_id": group["id"]},
        ).status_code == 200
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "brave-client", "provider_groups": {"brave": [group["id"]]}, "daily_limit": None},
    ).json()["relay_key"]
    return raw_key, group["id"]


def test_brave_get_relay_success(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client)
    captured = {}

    async def fake_request(self, method, url, params, content, headers):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        captured["content"] = content
        captured["headers"] = headers
        return httpx.Response(200, json={"web": {"results": [{"title": "brave-hit"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get("/brave/search?q=ai", headers={"Authorization": f"Bearer {raw_key}"})

    assert response.status_code == 200
    assert response.json() == {"web": {"results": [{"title": "brave-hit"}]}}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.search.brave.com/res/v1/search"
    assert captured["params"] == [("q", "ai")]
    assert captured["content"] is None
    assert captured["headers"]["X-Subscription-Token"] == "brave-primary-secret"
    assert "Authorization" not in captured["headers"]
    assert "x-api-key" not in captured["headers"]
    logs = recent_request_logs(client.app.state.db)
    assert logs[0]["provider"] == "brave"
    assert logs[0]["status_code"] == 200


def test_brave_forwarded_query_params(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client)
    captured = {}

    async def fake_request(self, method, url, params, content, headers):
        captured["params"] = params
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(f"/brave/search?q=llm&count=10&freshness=month&api_key={raw_key}")

    assert response.status_code == 200
    assert ("q", "llm") in captured["params"]
    assert ("count", "10") in captured["params"]
    assert ("freshness", "month") in captured["params"]
    # The relay credential is stripped and never forwarded upstream.
    assert not any(key == "api_key" for key, _ in captured["params"])
    assert all(raw_key not in value for _, value in captured["params"])


def test_brave_429_falls_back(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client, secrets=("brave-rate-limited", "brave-good"))
    used_keys = []

    async def fake_request(self, method, url, params, content, headers):
        used_keys.append(headers["X-Subscription-Token"])
        if headers["X-Subscription-Token"] == "brave-rate-limited":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"web": {"results": [{"title": "ok"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(
        "/brave/search?q=ai&no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    assert response.json() == {"web": {"results": [{"title": "ok"}]}}
    assert used_keys == ["brave-rate-limited", "brave-good"]


def test_brave_5xx_falls_back(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client, secrets=("brave-broken", "brave-good"))
    used_keys = []

    async def fake_request(self, method, url, params, content, headers):
        used_keys.append(headers["X-Subscription-Token"])
        if headers["X-Subscription-Token"] == "brave-broken":
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"web": {"results": [{"title": "ok"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(
        "/brave/search?q=ai&no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    assert response.json() == {"web": {"results": [{"title": "ok"}]}}
    assert used_keys == ["brave-broken", "brave-good"]


def test_brave_timeout_falls_back(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client, secrets=("brave-slow", "brave-fast"))
    used_keys = []

    async def fake_request(self, method, url, params, content, headers):
        used_keys.append(headers["X-Subscription-Token"])
        if headers["X-Subscription-Token"] == "brave-slow":
            raise httpx.TimeoutException("timed out")
        return httpx.Response(200, json={"web": {"results": [{"title": "ok"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(
        "/brave/search?q=ai&no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    assert response.json() == {"web": {"results": [{"title": "ok"}]}}
    assert used_keys == ["brave-slow", "brave-fast"]


def test_brave_key_failover(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client, secrets=("brave-invalid", "brave-valid"))
    used_keys = []

    async def fake_request(self, method, url, params, content, headers):
        used_keys.append(headers["X-Subscription-Token"])
        if headers["X-Subscription-Token"] == "brave-invalid":
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"web": {"results": [{"title": "ok"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(
        "/brave/search?q=ai&no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    assert response.json() == {"web": {"results": [{"title": "ok"}]}}
    assert used_keys == ["brave-invalid", "brave-valid"]
    providers = client.get("/api/admin/providers").json()["providers"]
    brave = next(provider for provider in providers if provider["name"] == "brave")
    invalid = next(key for key in brave["upstream_keys"] if key["label"] == "brave-key-0")
    assert invalid["is_invalid"] is True
    assert invalid["enabled"] is False


def test_brave_group_permissions(client):
    # add_relay_key binds only exa/tavily default groups — no brave group.
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/brave", json={"enabled": True}).status_code == 200

    response = client.get("/brave/search?q=ai", headers={"Authorization": f"Bearer {raw_key}"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provider_group_unassigned"


def test_brave_search_cache(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client)
    calls = 0

    async def fake_request(self, method, url, params, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"web": {"results": [{"title": f"call-{calls}"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    first = client.get("/brave/search?q=cached", headers={"Authorization": f"Bearer {raw_key}"})
    second = client.get("/brave/search?q=cached", headers={"Authorization": f"Bearer {raw_key}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1
    assert first.headers["x-search-relay-cache"] == "miss"
    assert second.headers["x-search-relay-cache"] == "hit"
    assert second.json() == {"web": {"results": [{"title": "call-1"}]}}


def _jina_relay_setup(client, secrets=("jina-primary-secret",), group_name="jina-vip"):
    """Admin-API setup for jina relay tests: enable the provider, create one
    jina group with the given upstream keys, and a relay key bound to that
    group. Returns (raw_relay_key, group_id).
    """
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/jina", json={"enabled": True}).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": group_name, "platform": "jina", "enabled": True},
    ).json()["group"]
    for index, secret in enumerate(secrets):
        assert client.post(
            "/api/admin/providers/jina/keys",
            json={"label": f"jina-key-{index}", "api_key": secret, "group_id": group["id"]},
        ).status_code == 200
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "jina-client", "provider_groups": {"jina": [group["id"]]}, "daily_limit": None},
    ).json()["relay_key"]
    return raw_key, group["id"]


def test_jina_rerank_success(client, monkeypatch):
    raw_key, _ = _jina_relay_setup(client)
    captured = {}

    async def fake_post(self, url, content, headers):
        captured["url"] = url
        captured["headers"] = headers
        captured["content"] = content
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    body = {"model": "jina-reranker-v2-base-multilingual", "query": "ai", "documents": ["doc1", "doc2"]}
    response = client.post(
        "/jina/rerank",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=body,
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"index": 0, "relevance_score": 0.9}]}
    assert captured["url"] == "https://api.jina.ai/v1/rerank"
    assert captured["headers"]["Authorization"] == "Bearer jina-primary-secret"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert json.loads(captured["content"]) == body
    logs = recent_request_logs(client.app.state.db)
    assert logs[0]["provider"] == "jina"
    assert logs[0]["status_code"] == 200


def test_jina_reader_success(client, monkeypatch):
    raw_key, _ = _jina_relay_setup(client)
    captured = {}

    async def fake_request(self, method, url, params, content, headers):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        captured["content"] = content
        captured["headers"] = headers
        return httpx.Response(200, content="# Article\n\nHello", headers={"content-type": "text/plain"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(
        "/jina/reader/https://example.com/article?x-locale=en",
        headers={
            "Authorization": f"Bearer {raw_key}",
            "X-Return-Format": "markdown",
            "Accept": "text/plain, text/markdown, */*",
        },
    )

    assert response.status_code == 200
    assert response.text == "# Article\n\nHello"
    assert captured["method"] == "GET"
    assert captured["url"] == "https://r.jina.ai/https://example.com/article"
    assert captured["params"] == [("x-locale", "en")]
    assert captured["content"] is None
    lower_headers = {name.lower(): value for name, value in captured["headers"].items()}
    assert lower_headers["x-return-format"] == "markdown"
    assert lower_headers["accept"] == "text/plain, text/markdown, */*"
    assert lower_headers["authorization"] == "Bearer jina-primary-secret"
    logs = recent_request_logs(client.app.state.db)
    assert logs[0]["provider"] == "jina"
    assert logs[0]["endpoint"] == "/reader/https://example.com/article"


def test_jina_reader_forwards_respond_with_header(client, monkeypatch):
    raw_key, _ = _jina_relay_setup(client)
    captured = {}

    async def fake_request(self, method, url, params, content, headers):
        captured["headers"] = headers
        return httpx.Response(200, content="ok")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    response = client.get(
        "/jina/reader/https://example.com/page",
        headers={"Authorization": f"Bearer {raw_key}", "X-Respond-With": "readerlm-v2"},
    )

    assert response.status_code == 200
    lower_headers = {name.lower(): value for name, value in captured["headers"].items()}
    assert lower_headers["x-respond-with"] == "readerlm-v2"
    assert lower_headers["authorization"] == "Bearer jina-primary-secret"


def test_jina_rerank_failure_falls_back(client, monkeypatch):
    raw_key, _ = _jina_relay_setup(client, secrets=("jina-broken", "jina-good"))
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer jina-broken":
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/jina/rerank",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "m", "query": "q", "documents": ["a"]},
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"index": 0, "relevance_score": 1.0}]}
    assert used_keys == ["Bearer jina-broken", "Bearer jina-good"]


def test_jina_key_failover(client, monkeypatch):
    raw_key, _ = _jina_relay_setup(client, secrets=("jina-invalid", "jina-valid"))
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer jina-invalid":
            return httpx.Response(401, json={"detail": "unauthorized"})
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/jina/rerank",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "m", "query": "q", "documents": ["a"]},
    )

    assert response.status_code == 200
    assert used_keys == ["Bearer jina-invalid", "Bearer jina-valid"]
    providers = client.get("/api/admin/providers").json()["providers"]
    jina = next(provider for provider in providers if provider["name"] == "jina")
    invalid = next(key for key in jina["upstream_keys"] if key["label"] == "jina-key-0")
    assert invalid["is_invalid"] is True
    assert invalid["enabled"] is False


def test_jina_group_permissions(client):
    # add_relay_key binds only exa/tavily default groups — no jina group.
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/jina", json={"enabled": True}).status_code == 200

    response = client.post(
        "/jina/rerank",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "m", "query": "q", "documents": ["a"]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provider_group_unassigned"


def test_jina_path_style_rejects_extra_segments_on_segment_routes(client):
    _, raw_key = add_relay_key(client)

    response = client.post(
        "/exa/search/extra",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unsupported_route"


def test_quota_exhausted_response_marks_exhausted(client, monkeypatch):
    # Upstream 432 (provider quota-exhausted code) marks the key exhausted
    # (used_quota == total_quota) and the relay fails over to the next key.
    # Exhaustion is not an invalidation — the key stays enabled, just at quota.
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "quota-bad", "api_key": "exa-quota-bad", "total_quota": 10},
    ).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "quota-good", "api_key": "exa-quota-good", "total_quota": 10},
    ).status_code == 200
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        if headers["x-api-key"] == "exa-quota-bad":
            return httpx.Response(432, json={"error": "quota exhausted"})
        return httpx.Response(200, json={"results": [{"title": "ok"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"title": "ok"}]}
    assert used_keys == ["exa-quota-bad", "exa-quota-good"]
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    bad = next(key for key in exa["upstream_keys"] if key["label"] == "quota-bad")
    assert bad["used_quota"] == bad["total_quota"] == 10
    assert bad["last_status_code"] == 432
    assert bad["enabled"] is True
    assert bad["is_invalid"] is False


def test_no_infinite_retry_when_all_keys_fail(client, monkeypatch):
    # Three keys all return 503: the loop terminates after exactly three
    # upstream calls (each key tried once) and the last upstream response is
    # passed through verbatim — no relay envelope, no unbounded retry.
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    for index in range(3):
        assert client.post(
            "/api/admin/providers/exa/keys",
            json={"label": f"broken-{index}", "api_key": f"exa-broken-{index}"},
        ).status_code == 200
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "temporary", "attempt": calls})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search?no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert calls == 3
    assert response.status_code == 503
    assert response.json() == {"error": "temporary", "attempt": 3}


def test_timeout_marks_error_and_fails_over(client, monkeypatch):
    # The first key times out -> marked with last_status_code 504; the relay
    # fails over to the second key which succeeds. A timeout is a temporary
    # error, not an invalidation, and does not consume quota.
    _, raw_key = add_relay_key(client)
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "slow", "api_key": "exa-slow"},
    ).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "fast", "api_key": "exa-fast"},
    ).status_code == 200
    used_keys = []

    async def fake_post(self, url, content, headers):
        used_keys.append(headers["x-api-key"])
        if headers["x-api-key"] == "exa-slow":
            raise httpx.TimeoutException("timed out")
        return httpx.Response(200, json={"results": [{"title": "ok"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search?no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )

    assert response.status_code == 200
    assert response.json() == {"results": [{"title": "ok"}]}
    assert used_keys == ["exa-slow", "exa-fast"]
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    slow = next(key for key in exa["upstream_keys"] if key["label"] == "slow")
    assert slow["last_status_code"] == 504
    assert slow["enabled"] is True
    assert slow["is_invalid"] is False
    assert slow["used_quota"] == 0


def test_relay_key_binds_multiple_providers_with_distinct_groups(client, monkeypatch):
    # One relay key binds one group per provider (exa/tavily/brave/jina).
    # Each provider's route may only use the upstream keys of its own group:
    # the URL and auth header per call must match that provider's binding only.
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    group_ids: dict[str, list[int]] = {}
    for provider in ("exa", "tavily", "brave", "jina"):
        assert client.put(f"/api/admin/providers/{provider}", json={"enabled": True}).status_code == 200
        group = client.post(
            "/api/admin/groups",
            json={"name": f"{provider}-iso", "platform": provider, "enabled": True},
        ).json()["group"]
        group_ids[provider] = [group["id"]]
        assert client.post(
            f"/api/admin/providers/{provider}/keys",
            json={"label": f"{provider}-key", "api_key": f"{provider}-upstream-secret", "group_id": group["id"]},
        ).status_code == 200
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "multi-provider", "provider_groups": group_ids, "daily_limit": None},
    ).json()["relay_key"]
    calls = []

    async def fake_post(self, url, content, headers):
        calls.append(("POST", url, headers))
        return httpx.Response(200, json={"ok": True})

    async def fake_request(self, method, url, params, content, headers):
        calls.append((method, url, headers))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    auth = {"Authorization": f"Bearer {raw_key}"}
    assert client.post("/exa/search?no_cache=true", headers=auth, json={"query": "ai"}).status_code == 200
    assert client.post("/tavily/search?no_cache=true", headers=auth, json={"query": "ai"}).status_code == 200
    assert client.get("/brave/search?q=ai&no_cache=true", headers=auth).status_code == 200
    assert client.post(
        "/jina/rerank?no_cache=true", headers=auth, json={"model": "m", "query": "q", "documents": ["a"]}
    ).status_code == 200

    by_url = {url: headers for _, url, headers in calls}
    assert set(by_url) == {
        "https://api.exa.ai/search",
        "https://api.tavily.com/search",
        "https://api.search.brave.com/res/v1/search",
        "https://api.jina.ai/v1/rerank",
    }
    assert by_url["https://api.exa.ai/search"]["x-api-key"] == "exa-upstream-secret"
    assert by_url["https://api.tavily.com/search"]["Authorization"] == "Bearer tavily-upstream-secret"
    assert by_url["https://api.search.brave.com/res/v1/search"]["X-Subscription-Token"] == "brave-upstream-secret"
    assert by_url["https://api.jina.ai/v1/rerank"]["Authorization"] == "Bearer jina-upstream-secret"
    # No cross-provider credential leakage: each call carries only its own auth.
    assert "Authorization" not in by_url["https://api.exa.ai/search"]
    assert "X-Subscription-Token" not in by_url["https://api.exa.ai/search"]
    assert "x-api-key" not in by_url["https://api.search.brave.com/res/v1/search"]
    assert "x-api-key" not in by_url["https://api.tavily.com/search"]


def test_brave_route_cannot_use_exa_group(client):
    # Strict provider isolation: a relay key bound only to an exa group must
    # not reach /brave/* — get_relay_key_provider_groups filters bindings by
    # platform, so no brave group resolves and the call is rejected.
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/brave", json={"enabled": True}).status_code == 200
    exa_group = client.post(
        "/api/admin/groups",
        json={"name": "exa-isolated", "platform": "exa", "enabled": True},
    ).json()["group"]
    raw_key = client.post(
        "/api/admin/relay-keys",
        json={"label": "exa-isolated-client", "provider_groups": {"exa": [exa_group["id"]]}, "daily_limit": None},
    ).json()["relay_key"]

    response = client.get("/brave/search?q=ai", headers={"Authorization": f"Bearer {raw_key}"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provider_group_unassigned"


def test_relay_logs_include_selected_upstream_key_id(client, monkeypatch):
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", "exa-secret", True)

    async def fake_post(self, url, content, headers):
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search?no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )
    assert response.status_code == 200

    log = recent_request_logs(client.app.state.db)[0]
    # Internal upstream key id is recorded — the selected key's id, never its value.
    assert isinstance(log["provider_key_id"], int)
    keys = list_provider_api_keys(client.app.state.db, "exa")
    assert log["provider_key_id"] == keys[0]["id"]


def test_logs_never_expose_upstream_key_value(client, monkeypatch):
    upstream_secret = "exa-upstream-secret-xyz"
    _, raw_key = add_relay_key(client)
    create_provider(client.app.state.db, "exa", upstream_secret, True)

    async def fake_post(self, url, content, headers):
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    response = client.post(
        "/exa/search?no_cache=true",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"query": "ai"},
    )
    assert response.status_code == 200

    log = recent_request_logs(client.app.state.db)[0]
    assert isinstance(log["provider_key_id"], int)
    # The raw upstream key value must never appear in any log column.
    for column, value in log.items():
        assert upstream_secret not in str(value), f"upstream key leaked in log column {column}"


def test_cache_applies_to_brave_search_route(client, monkeypatch):
    raw_key, _ = _brave_relay_setup(client)
    calls = 0

    async def fake_request(self, method, url, params, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"web": {"results": [{"title": f"hit-{calls}"}]}})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    first = client.get("/brave/search?q=ai", headers={"Authorization": f"Bearer {raw_key}"})
    assert first.status_code == 200
    assert first.headers.get("x-search-relay-cache") == "miss"

    second = client.get("/brave/search?q=ai", headers={"Authorization": f"Bearer {raw_key}"})
    assert second.status_code == 200
    assert second.headers.get("x-search-relay-cache") == "hit"

    assert calls == 1


def test_cache_does_not_apply_to_non_cacheable_route(client, monkeypatch):
    # Jina rerank is not cacheable (registry gate) — every request hits upstream.
    raw_key, _ = _jina_relay_setup(client)
    calls = 0

    async def fake_post(self, url, content, headers):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    body = {"model": "m", "query": "q", "documents": ["a"]}
    for _ in range(2):
        response = client.post(
            "/jina/rerank",
            headers={"Authorization": f"Bearer {raw_key}"},
            json=body,
        )
        assert response.status_code == 200

    assert calls == 2
    assert "x-search-relay-cache" not in response.headers
