import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    secret_key: str
    admin_password: str | None
    upstream_timeout_seconds: float
    search_cache_enabled: bool
    search_cache_ttl_seconds: int
    search_cache_max_rows: int


def load_settings() -> Settings:
    return Settings(
        database_path=os.getenv("APP_DATABASE_PATH", "data/search-relay.sqlite3"),
        secret_key=os.getenv("APP_SECRET_KEY", "dev-secret-change-me"),
        admin_password=os.getenv("ADMIN_PASSWORD"),
        upstream_timeout_seconds=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "60")),
        search_cache_enabled=os.getenv("SEARCH_CACHE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        search_cache_ttl_seconds=max(int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "43200")), 1),
        search_cache_max_rows=max(int(os.getenv("SEARCH_CACHE_MAX_ROWS", "10000")), 1),
    )
