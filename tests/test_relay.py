from datetime import datetime, timezone

import httpx

from app.repositories import create_provider, create_relay_key, list_search_cache_entries, recent_request_logs, set_cache_settings
from app.security import hash_secret


def add_relay_key(client, raw_key="relay_test_key", daily_limit=None):
    key_id = create_relay_key(client.app.state.db, "test", hash_secret(raw_key), daily_limit)
    return key_id, raw_key


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
