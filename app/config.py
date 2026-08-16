import os
from dataclasses import dataclass

DEFAULT_SECRET_KEY = "dev-secret-change-me"


def _bool_env(name: str, default: bool) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_path: str
    secret_key: str
    admin_password: str | None
    app_env: str
    cookie_secure: bool
    upstream_timeout_seconds: float
    max_upstream_attempts: int
    request_log_retention_days: int
    search_cache_enabled: bool
    search_cache_ttl_seconds: int
    search_cache_max_rows: int


def load_settings() -> Settings:
    return Settings(
        database_path=os.getenv("APP_DATABASE_PATH", "data/search-relay.sqlite3"),
        secret_key=os.getenv("APP_SECRET_KEY", DEFAULT_SECRET_KEY),
        admin_password=os.getenv("ADMIN_PASSWORD"),
        app_env=os.getenv("APP_ENV", "development").strip().lower(),
        cookie_secure=_bool_env("COOKIE_SECURE", default=False),
        upstream_timeout_seconds=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "60")),
        max_upstream_attempts=max(int(os.getenv("MAX_UPSTREAM_ATTEMPTS", "3")), 1),
        request_log_retention_days=max(int(os.getenv("REQUEST_LOG_RETENTION_DAYS", "30")), 1),
        search_cache_enabled=_bool_env("SEARCH_CACHE_ENABLED", default=True),
        search_cache_ttl_seconds=max(int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "43200")), 1),
        search_cache_max_rows=max(int(os.getenv("SEARCH_CACHE_MAX_ROWS", "10000")), 1),
    )
