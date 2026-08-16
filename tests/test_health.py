from app import main


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Additive provider-configuration block (Phase 15): every seeded provider
    # appears with configured + eligible_keys.
    assert isinstance(body["providers"], dict)
    assert set(body["providers"]) == {"brave", "exa", "jina", "tavily"}


def test_health_reports_provider_configuration(client):
    # Before any configuration, all providers are unconfigured with no keys.
    body = client.get("/health").json()
    for provider in ("exa", "tavily", "brave", "jina"):
        assert body["providers"][provider] == {"configured": False, "eligible_keys": 0}

    # Enable exa and add an upstream key — health reflects both tiers.
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "primary", "api_key": "exa-health-secret"},
    ).status_code == 200

    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["providers"]["exa"] == {"configured": True, "eligible_keys": 1}
    # Other providers untouched by exa configuration.
    assert body["providers"]["tavily"]["configured"] is False
    assert body["providers"]["brave"]["configured"] is False
    assert body["providers"]["jina"]["configured"] is False


def test_health_counts_only_eligible_keys(client):
    assert client.post("/api/admin/login", json={"password": "admin-test-password"}).status_code == 200
    assert client.put("/api/admin/providers/exa", json={"enabled": True}).status_code == 200
    assert client.post(
        "/api/admin/providers/exa/keys",
        json={"label": "primary", "api_key": "exa-eligible-secret"},
    ).status_code == 200
    # A disabled key must not count as eligible.
    providers = client.get("/api/admin/providers").json()["providers"]
    exa = next(provider for provider in providers if provider["name"] == "exa")
    key_id = exa["upstream_keys"][0]["id"]
    assert client.post(f"/api/admin/providers/exa/keys/{key_id}/disable").status_code == 200

    body = client.get("/health").json()
    assert body["providers"]["exa"] == {"configured": True, "eligible_keys": 0}


def test_admin_redirects_to_login_when_frontend_not_built(client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_INDEX", main.ADMIN_STATIC_DIR / "missing-index.html")

    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
