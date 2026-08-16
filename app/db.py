import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def connect(database_path: str) -> sqlite3.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS providers (
            name TEXT PRIMARY KEY,
            api_key TEXT,
            base_url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS provider_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_name TEXT NOT NULL,
            group_id INTEGER,
            label TEXT NOT NULL,
            api_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            total_quota INTEGER NOT NULL DEFAULT 1000,
            used_quota INTEGER NOT NULL DEFAULT 0,
            is_invalid INTEGER NOT NULL DEFAULT 0,
            use_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            last_error TEXT,
            last_status_code INTEGER,
            last_synced_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(provider_name) REFERENCES providers(name) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL DEFAULT 'exa',
            enabled INTEGER NOT NULL DEFAULT 1,
            daily_limit INTEGER,
            socks5_proxy TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS relay_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            group_id INTEGER,
            exa_group_id INTEGER,
            tavily_group_id INTEGER,
            key_value TEXT,
            key_hash TEXT NOT NULL UNIQUE,
            key_fingerprint TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            daily_limit INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(group_id) REFERENCES groups(id)
        );

        CREATE TABLE IF NOT EXISTS request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            provider TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            relay_key_id INTEGER,
            status_code INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            request_bytes INTEGER NOT NULL,
            response_bytes INTEGER NOT NULL,
            provider_group_id INTEGER,
            provider_group_name TEXT,
            error_code TEXT,
            error_message TEXT,
            FOREIGN KEY(relay_key_id) REFERENCES relay_keys(id)
        );

        CREATE TABLE IF NOT EXISTS search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            request_body TEXT NOT NULL,
            response_body TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO providers (name, base_url, enabled) VALUES (?, ?, 0)",
        ("exa", "https://api.exa.ai"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO providers (name, base_url, enabled) VALUES (?, ?, 0)",
        ("tavily", "https://api.tavily.com"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO providers (name, base_url, enabled) VALUES (?, ?, 0)",
        ("brave", "https://api.search.brave.com"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO providers (name, base_url, enabled) VALUES (?, ?, 0)",
        ("jina", "https://r.jina.ai"),
    )
    group_columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)").fetchall()}
    if "platform" not in group_columns:
        conn.execute("ALTER TABLE groups ADD COLUMN platform TEXT")
    if "socks5_proxy" not in group_columns:
        conn.execute("ALTER TABLE groups ADD COLUMN socks5_proxy TEXT")
    conn.execute("UPDATE groups SET platform = 'exa' WHERE platform IS NULL OR trim(platform) = ''")
    conn.execute("UPDATE groups SET platform = 'exa' WHERE name = 'default'")
    conn.execute(
        "INSERT OR IGNORE INTO groups (name, platform, enabled) VALUES (?, ?, 1)",
        ("default", "exa"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO groups (name, platform, enabled) VALUES (?, ?, 1)",
        ("tavily-default", "tavily"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO groups (name, platform, enabled) VALUES (?, ?, 1)",
        ("brave-default", "brave"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO groups (name, platform, enabled) VALUES (?, ?, 1)",
        ("jina-default", "jina"),
    )
    relay_columns = {row["name"] for row in conn.execute("PRAGMA table_info(relay_keys)").fetchall()}
    if "group_id" not in relay_columns:
        conn.execute("ALTER TABLE relay_keys ADD COLUMN group_id INTEGER")
    if "exa_group_id" not in relay_columns:
        conn.execute("ALTER TABLE relay_keys ADD COLUMN exa_group_id INTEGER")
    if "tavily_group_id" not in relay_columns:
        conn.execute("ALTER TABLE relay_keys ADD COLUMN tavily_group_id INTEGER")
    if "key_value" not in relay_columns:
        conn.execute("ALTER TABLE relay_keys ADD COLUMN key_value TEXT")
    if "key_fingerprint" not in relay_columns:
        # Indexed auth lookup (hardening release): nullable for legacy keys,
        # which fall back to the scan path until rotated. SQLite cannot add a
        # UNIQUE column via ALTER TABLE, so the constraint is a separate index.
        conn.execute("ALTER TABLE relay_keys ADD COLUMN key_fingerprint TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_keys_key_fingerprint ON relay_keys(key_fingerprint)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_key_groups (
            relay_key_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (relay_key_id, provider, group_id),
            FOREIGN KEY(relay_key_id) REFERENCES relay_keys(id) ON DELETE CASCADE,
            FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE
        )
        """
    )
    default_group = conn.execute("SELECT id FROM groups WHERE name = ?", ("default",)).fetchone()
    tavily_default_group = conn.execute("SELECT id FROM groups WHERE name = ?", ("tavily-default",)).fetchone()
    mapping_count = conn.execute("SELECT COUNT(*) AS count FROM relay_key_groups").fetchone()
    if int(mapping_count["count"] or 0) == 0:
        conn.execute(
            "UPDATE relay_keys SET group_id = ? WHERE group_id IS NULL",
            (default_group["id"],),
        )
        conn.execute(
            """
            UPDATE relay_keys
            SET exa_group_id = COALESCE(
                (
                    SELECT groups.id
                    FROM groups
                    WHERE groups.id = relay_keys.group_id
                      AND groups.platform = 'exa'
                ),
                ?
            )
            WHERE exa_group_id IS NULL
            """,
            (default_group["id"],),
        )
        conn.execute(
            "UPDATE relay_keys SET tavily_group_id = ? WHERE tavily_group_id IS NULL",
            (tavily_default_group["id"],),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO relay_key_groups (relay_key_id, provider, group_id, priority)
        SELECT id, 'exa', exa_group_id, 0
        FROM relay_keys
        WHERE exa_group_id IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO relay_key_groups (relay_key_id, provider, group_id, priority)
        SELECT id, 'tavily', tavily_group_id, 0
        FROM relay_keys
        WHERE tavily_group_id IS NOT NULL
        """
    )
    provider_key_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_api_keys)").fetchall()}
    if "group_id" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN group_id INTEGER")
    if "total_quota" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN total_quota INTEGER NOT NULL DEFAULT 1000")
    if "used_quota" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN used_quota INTEGER NOT NULL DEFAULT 0")
    if "is_invalid" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN is_invalid INTEGER NOT NULL DEFAULT 0")
    if "last_error" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN last_error TEXT")
    if "last_status_code" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN last_status_code INTEGER")
    if "last_synced_at" not in provider_key_columns:
        conn.execute("ALTER TABLE provider_api_keys ADD COLUMN last_synced_at TEXT")
    request_log_columns = {row["name"] for row in conn.execute("PRAGMA table_info(request_logs)").fetchall()}
    if "provider_group_id" not in request_log_columns:
        conn.execute("ALTER TABLE request_logs ADD COLUMN provider_group_id INTEGER")
    if "provider_group_name" not in request_log_columns:
        conn.execute("ALTER TABLE request_logs ADD COLUMN provider_group_name TEXT")
    if "provider_key_id" not in request_log_columns:
        # Internal id of the selected upstream key — never the key value.
        conn.execute("ALTER TABLE request_logs ADD COLUMN provider_key_id INTEGER")
    conn.execute(
        """
        INSERT INTO provider_api_keys (provider_name, group_id, label, api_key, enabled)
        SELECT
            providers.name,
            CASE providers.name
                WHEN 'exa' THEN ?
                WHEN 'tavily' THEN ?
            END,
            'Default',
            providers.api_key,
            1
        FROM providers
        WHERE providers.api_key IS NOT NULL
          AND trim(providers.api_key) != ''
          AND NOT EXISTS (
              SELECT 1 FROM provider_api_keys
              WHERE provider_api_keys.provider_name = providers.name
          )
        """
        ,
        (default_group["id"], tavily_default_group["id"]),
    )
    conn.execute(
        """
        UPDATE provider_api_keys
        SET group_id = CASE provider_name
            WHEN 'exa' THEN ?
            WHEN 'tavily' THEN ?
            ELSE group_id
        END
        WHERE group_id IS NULL
        """,
        (default_group["id"], tavily_default_group["id"]),
    )
    # request_logs indexes — created after the additive column migrations above
    # so legacy schemas (missing provider_group_id etc.) migrate first.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_relay_key_id ON request_logs(relay_key_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_request_logs_provider_group_created "
        "ON request_logs(provider, provider_group_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_request_logs_provider_created ON request_logs(provider, created_at)"
    )
    conn.commit()
