import logging
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.admin import api_router as admin_api_router
from app.config import load_settings
from app.db import connect, init_db
from app.repositories import get_setting, set_setting
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
    app.state.db = connect(app.state.settings.database_path)
    init_db(app.state.db)
    bootstrap_admin_password(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
