import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.admin import api_router as admin_api_router
from app.config import (
    KNOWN_PLACEHOLDER_ADMIN_PASSWORDS,
    KNOWN_PLACEHOLDER_SECRETS,
    load_settings,
)
from app.db import connect, init_db
from app.repositories import (
    count_eligible_provider_keys,
    get_setting,
    list_providers,
    prune_request_logs,
    set_setting,
)
from app.relay import router as relay_router
from app.security import hash_secret


logger = logging.getLogger(__name__)
ADMIN_STATIC_DIR = Path(__file__).resolve().parent / "static_admin"
ADMIN_INDEX = ADMIN_STATIC_DIR / "index.html"


def bootstrap_admin_password(app: FastAPI) -> None:
    if get_setting(app.state.db, "admin_password_hash"):
        return
    initial_password = app.state.settings.admin_password or secrets.token_urlsafe(18)
    set_setting(app.state.db, "admin_password_hash", hash_secret(initial_password))
    if not app.state.settings.admin_password:
        logger.warning("Generated initial admin password: %s", initial_password)


def create_app() -> FastAPI:
    app = FastAPI(title="Search Relay Platform")
    app.state.settings = load_settings()
    settings = app.state.settings
    production = settings.app_env in {"production", "prod"}
    if production:
        # Fail safer: the placeholders shipped in .env.example are public
        # knowledge (the repo is public). Refuse to boot with any of them so
        # a forgetful deployer cannot run with a forgeable session key or a
        # known admin password.
        problems: list[str] = []
        if settings.secret_key in KNOWN_PLACEHOLDER_SECRETS:
            problems.append("APP_SECRET_KEY is a known placeholder")
        if settings.admin_password and settings.admin_password in KNOWN_PLACEHOLDER_ADMIN_PASSWORDS:
            problems.append("ADMIN_PASSWORD is a known placeholder")
        if problems:
            raise RuntimeError(
                "Refusing to start in production: "
                + "; ".join(problems)
                + ". Set strong random values (e.g. `openssl rand -hex 32` for the secret key)."
            )
    else:
        if settings.secret_key in KNOWN_PLACEHOLDER_SECRETS:
            logger.warning(
                "APP_SECRET_KEY is a known placeholder; admin session cookies are signed "
                "with a predictable key. Set APP_SECRET_KEY before deploying."
            )
        if settings.admin_password and settings.admin_password in KNOWN_PLACEHOLDER_ADMIN_PASSWORDS:
            logger.warning(
                "ADMIN_PASSWORD is a known placeholder; use a strong random password before deploying."
            )
    app.state.db = connect(settings.database_path)
    init_db(app.state.db)
    bootstrap_admin_password(app)
    # Bounded request_logs: prune rows older than the retention window once at
    # startup (time-based, uses the created_at index).
    removed = prune_request_logs(app.state.db, app.state.settings.request_log_retention_days)
    if removed:
        logger.info("Pruned %d request_logs rows older than %d days", removed, app.state.settings.request_log_retention_days)

    @app.get("/health")
    def health() -> dict[str, Any]:
        # Two tiers: service liveness (top-level status) and per-provider
        # configuration status (configured + eligible upstream key count).
        # Cheap DB reads only — no upstream calls on ordinary health checks.
        providers = {
            str(row["name"]): {
                "configured": int(row["enabled"]) == 1,
                "eligible_keys": count_eligible_provider_keys(app.state.db, str(row["name"])),
            }
            for row in list_providers(app.state.db)
        }
        return {"status": "ok", "providers": providers}

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    if (ADMIN_STATIC_DIR / "assets").exists():
        app.mount("/admin/assets", StaticFiles(directory=ADMIN_STATIC_DIR / "assets"), name="admin-assets")

    @app.get("/admin", include_in_schema=False)
    def admin_index() -> Response:
        if ADMIN_INDEX.exists():
            return FileResponse(ADMIN_INDEX)
        return RedirectResponse("/admin/login", status_code=303)

    @app.get("/admin/{path:path}", include_in_schema=False)
    def admin_spa(path: str) -> Response:
        if ADMIN_INDEX.exists():
            return FileResponse(ADMIN_INDEX)
        return RedirectResponse("/admin/login", status_code=303)

    app.include_router(admin_api_router)
    app.include_router(relay_router)

    return app
