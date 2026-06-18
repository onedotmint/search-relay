from app import main


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_redirects_to_login_when_frontend_not_built(client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_INDEX", main.ADMIN_STATIC_DIR / "missing-index.html")

    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
