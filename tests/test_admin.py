import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import DEFAULT_SECRET_KEY
from app.repositories import create_relay_key, record_request_log, store_search_cache
from app.security import hash_secret



def api_login(client, password="admin-test-password"):
    return client.post("/api/admin/login", json={"password": password})


def test_admin_spa_falls_back_to_login_redirect_when_frontend_not_built(client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_INDEX", main.ADMIN_STATIC_DIR / "missing-index.html")

    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_spa_serves_index_when_frontend_is_built(client, monkeypatch, tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><div id='root'></div>", encoding="utf-8")
    monkeypatch.setattr(main, "ADMIN_INDEX", index)

    response = client.get("/admin/login")

    assert response.status_code == 200
    assert "<div id='root'></div>" in response.text


def test_api_admin_login_and_me(client):
    response = api_login(client)

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert "admin_session=" in response.headers["set-cookie"]

    me = client.get("/api/admin/me")
    assert me.status_code == 200
    assert me.json() == {"authenticated": True}


def test_login_cookie_not_secure_by_default(client):
    response = api_login(client)

    assert response.status_code == 200
    assert "Secure" not in response.headers["set-cookie"]


def _fresh_app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test-password")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return main.create_app()


def test_production_with_default_secret_refuses_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_SECRET_KEY", DEFAULT_SECRET_KEY)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test-password")

    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        main.create_app()


def test_dev_with_default_secret_boots_with_warning(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("APP_SECRET_KEY", DEFAULT_SECRET_KEY)
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test-password")

    with caplog.at_level(logging.WARNING, logger="app.main"):
        app = main.create_app()

    assert any("APP_SECRET_KEY" in record.message for record in caplog.records)
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200


def test_cookie_secure_flag_is_set_when_enabled(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, COOKIE_SECURE="true")

    with TestClient(app) as test_client:
        response = test_client.post("/api/admin/login", json={"password": "admin-test-password"})

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_api_login_secure_flag_off_by_default_in_dev(client):
    response = api_login(client)

    assert response.status_code == 200
    assert "Secure" not in response.headers["set-cookie"]


def test_providers_list_includes_brave(client):
    assert api_login(client).status_code == 200

    providers = client.get("/api/admin/providers").json()["providers"]
    names = [provider["name"] for provider in providers]

    # Brave is seeded from the registry alongside exa/tavily.
    assert "brave" in names
    brave = next(provider for provider in providers if provider["name"] == "brave")
    assert brave["base_url"] == "https://api.search.brave.com"
    assert brave["enabled"] is False
    assert brave["has_api_key"] is False
    assert brave["upstream_key_count"] == 0

    # Enabling brave + adding a group/key works through the generic admin API.
    assert client.put("/api/admin/providers/brave", json={"enabled": True}).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": "brave-admin", "platform": "brave", "enabled": True},
    ).json()["group"]
    assert group["platform"] == "brave"
    assert client.post(
        "/api/admin/providers/brave/keys",
        json={"label": "admin-key", "api_key": "brave-admin-secret", "group_id": group["id"]},
    ).status_code == 200

    updated = client.get("/api/admin/providers").json()["providers"]
    brave = next(provider for provider in updated if provider["name"] == "brave")
    assert brave["enabled"] is True
    assert brave["has_api_key"] is True
    assert brave["upstream_key_count"] == 1


def test_providers_list_includes_jina(client):
    assert api_login(client).status_code == 200

    providers = client.get("/api/admin/providers").json()["providers"]
    names = [provider["name"] for provider in providers]

    # Jina is seeded from the registry alongside exa/tavily/brave.
    assert "jina" in names
    jina = next(provider for provider in providers if provider["name"] == "jina")
    assert jina["base_url"] == "https://r.jina.ai"
    assert jina["enabled"] is False
    assert jina["has_api_key"] is False
    assert jina["upstream_key_count"] == 0

    # Enabling jina + adding a group/key works through the generic admin API.
    assert client.put("/api/admin/providers/jina", json={"enabled": True}).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": "jina-admin", "platform": "jina", "enabled": True},
    ).json()["group"]
    assert group["platform"] == "jina"
    assert client.post(
        "/api/admin/providers/jina/keys",
        json={"label": "admin-key", "api_key": "jina-admin-secret", "group_id": group["id"]},
    ).status_code == 200

    updated = client.get("/api/admin/providers").json()["providers"]
    jina = next(provider for provider in updated if provider["name"] == "jina")
    assert jina["enabled"] is True
    assert jina["has_api_key"] is True
    assert jina["upstream_key_count"] == 1


def test_admin_provider_list_is_dynamic_and_complete(client):
    """The admin provider list is dynamic and contains all four V1 providers.

    The frontend derives its provider selectors from this endpoint, so it must
    list exactly the registry-seeded providers (no frontend hardcode needed).
    """
    assert api_login(client).status_code == 200

    providers = client.get("/api/admin/providers").json()["providers"]
    names = [provider["name"] for provider in providers]

    assert names == ["brave", "exa", "jina", "tavily"]
    assert all("base_url" in provider for provider in providers)


def test_api_admin_wrong_password_is_rejected(client):
    response = api_login(client, "wrong")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_admin_password"


def test_api_admin_rejects_unauthenticated(client):
    response = client.get("/api/admin/dashboard")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "admin_auth_required"


def test_api_admin_provider_relay_logs_and_settings(client):
    assert api_login(client).status_code == 200

    provider_response = client.put("/api/admin/providers/exa", json={"api_key": "exa-secret", "enabled": True})
    assert provider_response.status_code == 200
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    assert exa["enabled"] is True
    assert exa["has_api_key"] is True
    assert "api_key" not in exa

    key_response = client.post("/api/admin/relay-keys", json={"label": "agent", "daily_limit": 25})
    assert key_response.status_code == 200
    created_key = key_response.json()["relay_key"]
    assert created_key.startswith("relay_")
    keys = client.get("/api/admin/relay-keys").json()["relay_keys"]
    assert keys[0]["label"] == "agent"

    assert client.post(f"/api/admin/relay-keys/{keys[0]['id']}/disable").status_code == 200
    assert client.post(f"/api/admin/relay-keys/{keys[0]['id']}/enable").status_code == 200

    dashboard = client.get("/api/admin/dashboard")
    logs = client.get("/api/admin/logs")
    assert dashboard.status_code == 200
    assert "metrics" in dashboard.json()
    assert logs.status_code == 200
    assert "logs" in logs.json()

    password_response = client.post(
        "/api/admin/settings/password",
        json={"current_password": "admin-test-password", "new_password": "new-admin-password"},
    )
    assert password_response.status_code == 200
    client.post("/api/admin/logout")
    assert api_login(client, "new-admin-password").status_code == 200


def test_api_admin_password_change_requires_current_password(client):
    assert api_login(client).status_code == 200

    rejected = client.post(
        "/api/admin/settings/password",
        json={"current_password": "wrong-password", "new_password": "new-admin-password"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "invalid_current_password"

    client.post("/api/admin/logout")
    assert api_login(client, "admin-test-password").status_code == 200

    accepted = client.post(
        "/api/admin/settings/password",
        json={"current_password": "admin-test-password", "new_password": "new-admin-password"},
    )
    assert accepted.status_code == 200
    client.post("/api/admin/logout")
    assert api_login(client, "new-admin-password").status_code == 200


def test_api_admin_logs_support_filters_pagination_and_detail(client):
    assert api_login(client).status_code == 200
    key_id = create_relay_key(client.app.state.db, "agent", hash_secret("relay-log-key"), None)
    record_request_log(
        client.app.state.db,
        provider="exa",
        endpoint="/search",
        relay_key_id=key_id,
        status_code=200,
        duration_ms=20,
        request_bytes=10,
        response_bytes=30,
        error_code=None,
        error_message=None,
    )
    record_request_log(
        client.app.state.db,
        provider="exa",
        endpoint="/search",
        relay_key_id=key_id,
        status_code=504,
        duration_ms=900,
        request_bytes=11,
        response_bytes=0,
        error_code="upstream_timeout",
        error_message="upstream timeout while searching",
    )
    record_request_log(
        client.app.state.db,
        provider="tavily",
        endpoint="/extract",
        relay_key_id=None,
        status_code=429,
        duration_ms=70,
        request_bytes=12,
        response_bytes=40,
        error_code="upstream_error",
        error_message="rate limited",
    )

    filtered = client.get("/api/admin/logs?provider=exa&status=error&q=timeout&limit=1&offset=0")
    assert filtered.status_code == 200
    payload = filtered.json()
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert len(payload["logs"]) == 1
    assert payload["logs"][0]["status_code"] == 504
    assert payload["logs"][0]["relay_key_label"] == "agent"
    assert payload["logs"][0]["error_message"] == "upstream timeout while searching"

    detail = client.get(f"/api/admin/logs/{payload['logs'][0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["log"]["error_code"] == "upstream_timeout"


def test_api_admin_can_edit_groups_provider_keys_and_relay_keys(client):
    assert api_login(client).status_code == 200

    exa_group = client.post("/api/admin/groups", json={"name": "exa-edit", "platform": "exa"}).json()["group"]
    exa_replacement = client.post("/api/admin/groups", json={"name": "exa-replacement", "platform": "exa"}).json()["group"]
    tavily_group = client.post("/api/admin/groups", json={"name": "tavily-edit", "platform": "tavily"}).json()["group"]

    group_update = client.put(
        f"/api/admin/groups/{exa_group['id']}",
        json={"name": "exa-renamed", "enabled": False},
    )
    assert group_update.status_code == 200
    assert group_update.json()["group"]["name"] == "exa-renamed"
    assert group_update.json()["group"]["enabled"] is False
    assert group_update.json()["group"]["platform"] == "exa"

    provider_key_response = client.post(
        "/api/admin/providers/exa/keys",
        json={
            "label": "primary",
            "api_key": "exa-original",
            "group_id": exa_group["id"],
            "total_quota": 100,
        },
    )
    provider_key = provider_key_response.json()["upstream_key"]
    provider_key_update = client.put(
        f"/api/admin/providers/exa/keys/{provider_key['id']}",
        json={
            "label": "primary-renamed",
            "group_id": exa_replacement["id"],
            "total_quota": 250,
            "enabled": False,
        },
    )
    assert provider_key_update.status_code == 200
    updated_provider_key = provider_key_update.json()["upstream_key"]
    assert updated_provider_key["label"] == "primary-renamed"
    assert updated_provider_key["group_id"] == exa_replacement["id"]
    assert updated_provider_key["enabled"] is False
    assert updated_provider_key["total_quota"] == 250
    assert updated_provider_key["key_preview"] == "...inal"

    provider_key_secret_update = client.put(
        f"/api/admin/providers/exa/keys/{provider_key['id']}",
        json={
            "label": "primary-renamed",
            "api_key": "exa-replacement-secret",
            "group_id": exa_replacement["id"],
            "total_quota": 250,
            "enabled": True,
        },
    )
    assert provider_key_secret_update.status_code == 200
    assert provider_key_secret_update.json()["upstream_key"]["key_preview"] == "...cret"

    relay_key_response = client.post(
        "/api/admin/relay-keys",
        json={
            "label": "agent",
            "exa_group_id": exa_group["id"],
            "tavily_group_id": tavily_group["id"],
            "daily_limit": 50,
        },
    )
    relay_record = relay_key_response.json()["record"]
    relay_update = client.put(
        f"/api/admin/relay-keys/{relay_record['id']}",
        json={
            "label": "agent-renamed",
            "exa_group_id": exa_replacement["id"],
            "tavily_group_id": None,
            "daily_limit": None,
            "enabled": False,
        },
    )
    assert relay_update.status_code == 200
    updated_relay = relay_update.json()["relay_key"]
    assert updated_relay["label"] == "agent-renamed"
    assert updated_relay["exa_group_id"] == exa_replacement["id"]
    assert updated_relay["tavily_group_id"] is None
    assert updated_relay["daily_limit"] is None
    assert updated_relay["enabled"] is False


def test_api_admin_rejects_editing_keys_with_wrong_platform_group(client):
    assert api_login(client).status_code == 200

    exa_group = client.post("/api/admin/groups", json={"name": "exa-edit", "platform": "exa"}).json()["group"]
    tavily_group = client.post("/api/admin/groups", json={"name": "tavily-edit", "platform": "tavily"}).json()["group"]

    provider_key = client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "primary", "api_key": "exa-original", "group_id": exa_group["id"]},
    ).json()["upstream_key"]
    provider_key_update = client.put(
        f"/api/admin/providers/exa/keys/{provider_key['id']}",
        json={
            "label": "primary",
            "group_id": tavily_group["id"],
            "total_quota": 100,
            "enabled": True,
        },
    )
    assert provider_key_update.status_code == 400
    assert provider_key_update.json()["error"]["code"] == "group_platform_mismatch"

    relay_record = client.post(
        "/api/admin/relay-keys",
        json={"label": "agent", "exa_group_id": exa_group["id"], "tavily_group_id": tavily_group["id"]},
    ).json()["record"]
    relay_update = client.put(
        f"/api/admin/relay-keys/{relay_record['id']}",
        json={
            "label": "agent",
            "exa_group_id": tavily_group["id"],
            "tavily_group_id": tavily_group["id"],
            "daily_limit": None,
            "enabled": True,
        },
    )
    assert relay_update.status_code == 400
    assert relay_update.json()["error"]["code"] == "group_platform_mismatch"


def test_api_admin_can_manage_cache_center(client):
    assert api_login(client).status_code == 200
    store_search_cache(
        client.app.state.db,
        "active-key",
        "exa",
        "search",
        b'{"query":"active"}',
        b'{"results":["active"]}',
        200,
        "application/json",
        ttl_seconds=60,
    )
    store_search_cache(
        client.app.state.db,
        "expired-key",
        "tavily",
        "search",
        b'{"query":"expired"}',
        b'{"results":["expired"]}',
        200,
        "application/json",
        ttl_seconds=-60,
    )

    settings_response = client.get("/api/admin/cache/settings")
    assert settings_response.status_code == 200
    assert settings_response.json()["settings"] == {"enabled": True, "ttl_seconds": 43200, "max_rows": 10000}

    update_settings = client.put(
        "/api/admin/cache/settings",
        json={"enabled": False, "ttl_seconds": 300, "max_rows": 20},
    )
    assert update_settings.status_code == 200
    assert update_settings.json()["settings"] == {"enabled": False, "ttl_seconds": 300, "max_rows": 20}

    stats = client.get("/api/admin/cache/stats")
    assert stats.status_code == 200
    assert stats.json()["stats"]["total"] == 2
    assert stats.json()["stats"]["active"] == 1
    assert stats.json()["stats"]["expired"] == 1

    entries = client.get("/api/admin/cache")
    assert entries.status_code == 200
    assert entries.json()["total"] == 2
    active_entry = next(entry for entry in entries.json()["entries"] if entry["cache_key"] == "active-key")
    exa_entries = client.get("/api/admin/cache?provider=exa")
    assert exa_entries.status_code == 200
    assert exa_entries.json()["total"] == 1
    assert exa_entries.json()["entries"][0]["provider"] == "exa"

    delete_response = client.delete(f"/api/admin/cache/{active_entry['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}

    prune_response = client.post("/api/admin/cache/prune")
    assert prune_response.status_code == 200
    assert prune_response.json()["deleted"] == 1

    clear_response = client.post("/api/admin/cache/clear")
    assert clear_response.status_code == 200
    assert clear_response.json()["deleted"] == 0


def test_api_admin_can_manage_multiple_upstream_provider_keys(client):
    assert api_login(client).status_code == 200

    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    first = client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "primary", "api_key": "exa-primary-secret"},
    )
    second = client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "backup", "api_key": "exa-backup-secret"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    assert exa["enabled"] is True
    assert exa["upstream_key_count"] == 2
    assert exa["has_api_key"] is True
    assert [key["label"] for key in exa["upstream_keys"]] == ["backup", "primary"]
    assert exa["upstream_keys"][0]["key_preview"] == "...cret"
    assert "api_key" not in exa["upstream_keys"][0]

    backup_id = exa["upstream_keys"][0]["id"]
    disable_response = client.post(f"/api/admin/providers/exa/keys/{backup_id}/disable")
    assert disable_response.status_code == 200
    updated = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in updated if provider["name"] == "exa")
    backup = next(key for key in exa["upstream_keys"] if key["id"] == backup_id)
    assert backup["enabled"] is False


def test_api_admin_can_manage_platform_groups_and_assign_external_keys(client):
    assert api_login(client).status_code == 200

    exa_group_response = client.post("/api/admin/groups", json={"name": "exa-vip", "platform": "exa", "enabled": True})
    tavily_group_response = client.post(
        "/api/admin/groups",
        json={"name": "tavily-vip", "platform": "tavily", "enabled": True},
    )
    assert exa_group_response.status_code == 200
    assert tavily_group_response.status_code == 200
    exa_group = exa_group_response.json()["group"]
    tavily_group = tavily_group_response.json()["group"]
    assert exa_group["name"] == "exa-vip"
    assert exa_group["platform"] == "exa"
    assert "daily_limit" not in exa_group

    key_response = client.post(
        "/api/admin/relay-keys",
        json={
            "label": "vip-client",
            "exa_group_id": exa_group["id"],
            "tavily_group_id": tavily_group["id"],
            "daily_limit": 25,
        },
    )
    assert key_response.status_code == 200
    keys = client.get("/api/admin/relay-keys").json()["relay_keys"]
    created = next(key for key in keys if key["label"] == "vip-client")
    assert created["exa_group_id"] == exa_group["id"]
    assert created["exa_group_name"] == "exa-vip"
    assert created["tavily_group_id"] == tavily_group["id"]
    assert created["tavily_group_name"] == "tavily-vip"

    disable_response = client.post(f"/api/admin/groups/{exa_group['id']}/disable")
    assert disable_response.status_code == 200
    groups = client.get("/api/admin/groups").json()["groups"]
    vip = next(item for item in groups if item["id"] == exa_group["id"])
    assert vip["enabled"] is False


def test_api_admin_group_proxy_and_multi_group_external_key(client):
    assert api_login(client).status_code == 200

    exa_a = client.post(
        "/api/admin/groups",
        json={"name": "exa-a", "platform": "exa", "enabled": True, "socks5_proxy": "socks5://127.0.0.1:1080"},
    ).json()["group"]
    exa_b = client.post(
        "/api/admin/groups",
        json={"name": "exa-b", "platform": "exa", "enabled": True, "socks5_proxy": ""},
    ).json()["group"]
    tavily = client.post(
        "/api/admin/groups",
        json={"name": "tavily-a", "platform": "tavily", "enabled": True, "socks5_proxy": "socks5h://proxy.example.com:1080"},
    ).json()["group"]

    assert exa_a["socks5_proxy"] == "socks5://127.0.0.1:1080"
    assert exa_b["socks5_proxy"] is None
    assert tavily["socks5_proxy"] == "socks5h://proxy.example.com:1080"

    created_response = client.post(
        "/api/admin/relay-keys",
        json={
            "label": "multi-client",
            "exa_group_ids": [exa_a["id"], exa_b["id"]],
            "tavily_group_ids": [tavily["id"]],
            "daily_limit": None,
        },
    )
    assert created_response.status_code == 200
    record = created_response.json()["record"]
    assert [group["id"] for group in record["exa_groups"]] == [exa_a["id"], exa_b["id"]]
    assert [group["id"] for group in record["tavily_groups"]] == [tavily["id"]]
    assert record["exa_group_id"] == exa_a["id"]
    assert record["tavily_group_id"] == tavily["id"]

    update_response = client.put(
        f"/api/admin/relay-keys/{record['id']}",
        json={
            "label": "multi-client-renamed",
            "exa_group_ids": [exa_b["id"]],
            "tavily_group_ids": [],
            "daily_limit": 10,
            "enabled": False,
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()["relay_key"]
    assert updated["label"] == "multi-client-renamed"
    assert [group["id"] for group in updated["exa_groups"]] == [exa_b["id"]]
    assert updated["tavily_groups"] == []
    assert updated["enabled"] is False


def test_api_admin_rejects_invalid_group_proxy(client):
    assert api_login(client).status_code == 200

    response = client.post(
        "/api/admin/groups",
        json={"name": "bad-proxy", "platform": "exa", "socks5_proxy": "http://127.0.0.1:8080"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_group_proxy"


def test_api_admin_rejects_invalid_external_key_group(client):
    assert api_login(client).status_code == 200

    response = client.post(
        "/api/admin/relay-keys",
        json={"label": "bad-client", "exa_group_id": 99999, "daily_limit": None},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "group_not_found"


def test_api_create_relay_key_with_provider_groups_payload(client):
    assert api_login(client).status_code == 200
    exa_group = client.post("/api/admin/groups", json={"name": "exa-pg", "platform": "exa"}).json()["group"]

    response = client.post(
        "/api/admin/relay-keys",
        json={"label": "pg-client", "provider_groups": {"exa": [exa_group["id"]]}},
    )

    assert response.status_code == 200
    record = response.json()["record"]
    assert [group["id"] for group in record["provider_groups"]["exa"]] == [exa_group["id"]]
    assert record["exa_group_id"] == exa_group["id"]


def test_api_relay_key_public_includes_provider_groups(client):
    assert api_login(client).status_code == 200
    exa_group = client.post("/api/admin/groups", json={"name": "exa-pub", "platform": "exa"}).json()["group"]
    tavily_group = client.post(
        "/api/admin/groups", json={"name": "tavily-pub", "platform": "tavily"}
    ).json()["group"]

    created = client.post(
        "/api/admin/relay-keys",
        json={
            "label": "pub-client",
            "provider_groups": {"exa": [exa_group["id"]], "tavily": [tavily_group["id"]]},
        },
    ).json()["record"]

    keys = client.get("/api/admin/relay-keys").json()["relay_keys"]
    listed = next(key for key in keys if key["id"] == created["id"])
    assert [group["id"] for group in listed["provider_groups"]["exa"]] == [exa_group["id"]]
    assert [group["id"] for group in listed["provider_groups"]["tavily"]] == [tavily_group["id"]]
    assert [group["id"] for group in listed["exa_groups"]] == [exa_group["id"]]


def test_api_update_relay_key_with_provider_groups(client):
    assert api_login(client).status_code == 200
    exa_group = client.post("/api/admin/groups", json={"name": "exa-up", "platform": "exa"}).json()["group"]
    tavily_group = client.post(
        "/api/admin/groups", json={"name": "tavily-up", "platform": "tavily"}
    ).json()["group"]

    created = client.post(
        "/api/admin/relay-keys",
        json={"label": "up-client", "provider_groups": {"exa": [exa_group["id"]]}},
    ).json()["record"]

    update = client.put(
        f"/api/admin/relay-keys/{created['id']}",
        json={
            "label": "up-client",
            "provider_groups": {"exa": [exa_group["id"]], "tavily": [tavily_group["id"]]},
            "enabled": True,
        },
    )

    assert update.status_code == 200
    updated = update.json()["relay_key"]
    assert [group["id"] for group in updated["provider_groups"]["exa"]] == [exa_group["id"]]
    assert [group["id"] for group in updated["provider_groups"]["tavily"]] == [tavily_group["id"]]


def test_api_provider_groups_override_legacy_fields(client):
    assert api_login(client).status_code == 200
    exa_a = client.post("/api/admin/groups", json={"name": "exa-a", "platform": "exa"}).json()["group"]
    exa_b = client.post("/api/admin/groups", json={"name": "exa-b", "platform": "exa"}).json()["group"]

    response = client.post(
        "/api/admin/relay-keys",
        json={
            "label": "override",
            "exa_group_id": exa_a["id"],
            "provider_groups": {"exa": [exa_b["id"]]},
        },
    )

    assert response.status_code == 200
    record = response.json()["record"]
    assert [group["id"] for group in record["provider_groups"]["exa"]] == [exa_b["id"]]


def test_api_rejects_unknown_provider_in_provider_groups_payload(client):
    assert api_login(client).status_code == 200

    response = client.post(
        "/api/admin/relay-keys",
        json={"label": "bad-provider", "provider_groups": {"nope": [1]}},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "provider_not_found"


def test_api_rejects_wrong_platform_group_in_provider_groups_payload(client):
    assert api_login(client).status_code == 200
    tavily_group = client.post(
        "/api/admin/groups", json={"name": "tavily-wrong", "platform": "tavily"}
    ).json()["group"]

    response = client.post(
        "/api/admin/relay-keys",
        json={"label": "mismatch", "provider_groups": {"exa": [tavily_group["id"]]}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "group_platform_mismatch"


def test_api_admin_rejects_empty_external_key_label(client):
    assert api_login(client).status_code == 200

    response = client.post("/api/admin/relay-keys", json={"label": "   ", "daily_limit": None})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_relay_key"


def test_api_admin_can_copy_and_delete_external_key(client):
    assert api_login(client).status_code == 200

    create_response = client.post(
        "/api/admin/relay-keys",
        json={"label": "copy-client", "daily_limit": None},
    )

    assert create_response.status_code == 200
    created_key = create_response.json()["relay_key"]
    created_record = create_response.json()["record"]
    assert created_record["key_preview"] == f"...{created_key[-4:]}"
    assert created_record["has_key_value"] is True
    assert "key_value" not in created_record

    list_response = client.get("/api/admin/relay-keys")
    listed = next(key for key in list_response.json()["relay_keys"] if key["label"] == "copy-client")
    assert listed["key_preview"] == f"...{created_key[-4:]}"
    assert listed["has_key_value"] is True
    assert "key_value" not in listed

    value_response = client.get(f"/api/admin/relay-keys/{listed['id']}/value")
    assert value_response.status_code == 200
    assert value_response.json() == {"relay_key": created_key}

    delete_response = client.delete(f"/api/admin/relay-keys/{listed['id']}")
    assert delete_response.status_code == 200
    remaining = client.get("/api/admin/relay-keys").json()["relay_keys"]
    assert all(key["id"] != listed["id"] for key in remaining)


def test_api_admin_provider_key_group_must_match_platform(client):
    assert api_login(client).status_code == 200

    tavily_group = client.post(
        "/api/admin/groups",
        json={"name": "tavily-only", "platform": "tavily", "enabled": True},
    ).json()["group"]

    response = client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "wrong", "api_key": "exa-secret", "group_id": tavily_group["id"]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "group_platform_mismatch"


def test_api_admin_provider_key_records_platform_group(client):
    assert api_login(client).status_code == 200
    group = client.post(
        "/api/admin/groups",
        json={"name": "exa-pool", "platform": "exa", "enabled": True},
    ).json()["group"]

    response = client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "pool-key", "api_key": "exa-secret", "group_id": group["id"]},
    )

    assert response.status_code == 200
    upstream_key = response.json()["upstream_key"]
    assert upstream_key["group_id"] == group["id"]
    assert upstream_key["group_name"] == "exa-pool"


def test_api_admin_provider_key_returns_quota_status_fields(client):
    assert api_login(client).status_code == 200

    response = client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "quota-key", "api_key": "exa-secret", "total_quota": 250},
    )

    assert response.status_code == 200
    upstream_key = response.json()["upstream_key"]
    assert upstream_key["total_quota"] == 250
    assert upstream_key["used_quota"] == 0
    assert upstream_key["remaining_quota"] == 250
    assert upstream_key["is_invalid"] is False
    assert upstream_key["last_error"] is None
    assert upstream_key["last_status_code"] is None
    assert upstream_key["last_synced_at"] is None


def test_api_admin_syncs_tavily_usage(client, monkeypatch):
    assert api_login(client).status_code == 200
    assert client.put("/api/admin/providers/tavily", json={"enabled": True}).status_code == 200
    response = client.post(
        "/api/admin/providers/tavily/keys",
        json={"label": "usage-key", "api_key": "tvly-secret", "total_quota": 100},
    )
    key_id = response.json()["upstream_key"]["id"]
    captured = {}

    async def fake_get(self, url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return httpx.Response(200, json={"key": {"usage": 42, "limit": 200}})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    sync_response = client.post(f"/api/admin/providers/tavily/keys/{key_id}/sync-usage")

    assert sync_response.status_code == 200
    assert captured["url"] == "https://api.tavily.com/usage"
    assert captured["headers"]["Authorization"] == "Bearer tvly-secret"
    synced = sync_response.json()["upstream_key"]
    assert synced["used_quota"] == 42
    assert synced["total_quota"] == 200
    assert synced["remaining_quota"] == 158
    assert synced["last_synced_at"] is not None
