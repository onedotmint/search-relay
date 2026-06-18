import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_GROUP_NAMES = {
    "exa": "default",
    "tavily": "tavily-default",
}


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def list_providers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM providers ORDER BY name").fetchall()]


def get_provider(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM providers WHERE name = ?", (name,)).fetchone())


def create_provider(conn: sqlite3.Connection, name: str, api_key: str, enabled: bool) -> None:
    base_url = "https://api.exa.ai" if name == "exa" else "https://api.tavily.com"
    api_key = api_key.strip()
    group_id = get_default_group_id(conn, name)
    conn.execute(
        """
        INSERT INTO providers (name, api_key, base_url, enabled, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            api_key = excluded.api_key,
            base_url = excluded.base_url,
            enabled = excluded.enabled,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, api_key, base_url, 1 if enabled else 0),
    )
    if api_key:
        existing = conn.execute(
            """
            SELECT id FROM provider_api_keys
            WHERE provider_name = ? AND label = 'Default'
            """,
            (name,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE provider_api_keys
                SET api_key = ?, group_id = ?, enabled = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (api_key, group_id, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO provider_api_keys (provider_name, group_id, label, api_key, enabled)
                VALUES (?, ?, 'Default', ?, 1)
                """,
                (name, group_id, api_key),
            )
    conn.commit()


def key_preview(api_key: str) -> str:
    return f"...{api_key[-4:]}" if api_key else ""


def normalize_group_ids(group_ids: list[int] | tuple[int, ...] | None) -> list[int]:
    if not group_ids:
        return []
    normalized: list[int] = []
    for group_id in group_ids:
        value = int(group_id)
        if value not in normalized:
            normalized.append(value)
    return normalized


def list_provider_api_keys(conn: sqlite3.Connection, provider_name: str | None = None) -> list[dict[str, Any]]:
    if provider_name is None:
        rows = conn.execute(
            """
            SELECT provider_api_keys.*, groups.name AS group_name, groups.platform AS group_platform
            FROM provider_api_keys
            LEFT JOIN groups ON groups.id = provider_api_keys.group_id
            ORDER BY provider_api_keys.provider_name, provider_api_keys.id DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT provider_api_keys.*, groups.name AS group_name, groups.platform AS group_platform
            FROM provider_api_keys
            LEFT JOIN groups ON groups.id = provider_api_keys.group_id
            WHERE provider_api_keys.provider_name = ?
            ORDER BY provider_api_keys.id DESC
            """,
            (provider_name,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_provider_api_key(
    conn: sqlite3.Connection,
    provider_name: str,
    label: str,
    api_key: str,
    enabled: bool = True,
    group_id: int | None = None,
    total_quota: int = 1000,
) -> int:
    assigned_group_id = group_id if group_id is not None else get_default_group_id(conn, provider_name)
    normalized_total_quota = max(int(total_quota or 1000), 1)
    cursor = conn.execute(
        """
        INSERT INTO provider_api_keys (provider_name, group_id, label, api_key, enabled, total_quota)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (provider_name, assigned_group_id, label.strip(), api_key.strip(), 1 if enabled else 0, normalized_total_quota),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_provider_api_key(
    conn: sqlite3.Connection,
    provider_name: str,
    key_id: int,
    label: str,
    api_key: str | None,
    enabled: bool,
    group_id: int | None,
    total_quota: int,
) -> None:
    normalized_total_quota = max(int(total_quota or 1000), 1)
    new_api_key = api_key.strip() if api_key else ""
    if new_api_key:
        conn.execute(
            """
            UPDATE provider_api_keys
            SET label = ?,
                api_key = ?,
                enabled = ?,
                group_id = ?,
                total_quota = ?,
                is_invalid = 0,
                last_error = NULL,
                last_status_code = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND provider_name = ?
            """,
            (
                label.strip(),
                new_api_key,
                1 if enabled else 0,
                group_id,
                normalized_total_quota,
                key_id,
                provider_name,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE provider_api_keys
            SET label = ?,
                enabled = ?,
                group_id = ?,
                total_quota = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND provider_name = ?
            """,
            (
                label.strip(),
                1 if enabled else 0,
                group_id,
                normalized_total_quota,
                key_id,
                provider_name,
            ),
        )
    conn.commit()


def set_provider_api_key_enabled(conn: sqlite3.Connection, provider_name: str, key_id: int, enabled: bool) -> None:
    conn.execute(
        """
        UPDATE provider_api_keys
        SET enabled = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND provider_name = ?
        """,
        (1 if enabled else 0, key_id, provider_name),
    )
    conn.commit()


def delete_provider_api_key(conn: sqlite3.Connection, provider_name: str, key_id: int) -> None:
    conn.execute(
        "DELETE FROM provider_api_keys WHERE id = ? AND provider_name = ?",
        (key_id, provider_name),
    )
    conn.commit()


def get_next_provider_api_key(
    conn: sqlite3.Connection,
    provider_name: str,
    group_id: int | None = None,
) -> dict[str, Any] | None:
    candidates = get_candidate_provider_api_keys(conn, provider_name, group_id)
    return candidates[0] if candidates else None


def get_candidate_provider_api_keys(
    conn: sqlite3.Connection,
    provider_name: str,
    group_id: int | None = None,
) -> list[dict[str, Any]]:
    group_filter = "" if group_id is None else "AND provider_api_keys.group_id = ?"
    params: tuple[Any, ...] = (provider_name,) if group_id is None else (provider_name, group_id)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT provider_api_keys.*
            FROM provider_api_keys
            JOIN providers ON providers.name = provider_api_keys.provider_name
            WHERE provider_api_keys.provider_name = ?
              AND provider_api_keys.enabled = 1
              AND provider_api_keys.is_invalid = 0
              AND provider_api_keys.used_quota < provider_api_keys.total_quota
              AND providers.enabled = 1
              {group_filter}
            ORDER BY (provider_api_keys.total_quota - provider_api_keys.used_quota) DESC,
                     provider_api_keys.use_count ASC,
                     COALESCE(provider_api_keys.last_used_at, '') ASC,
                     provider_api_keys.id ASC
            """,
            params,
        ).fetchall()
    ]


def mark_provider_api_key_used(conn: sqlite3.Connection, key_id: int) -> None:
    conn.execute(
        """
        UPDATE provider_api_keys
        SET use_count = use_count + 1,
            used_quota = CASE
                WHEN used_quota + 1 > total_quota THEN total_quota
                ELSE used_quota + 1
            END,
            last_used_at = ?,
            last_error = NULL,
            last_status_code = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (datetime.now(timezone.utc).isoformat(timespec="microseconds"), key_id),
    )
    conn.commit()


def mark_provider_api_key_error(
    conn: sqlite3.Connection,
    key_id: int,
    status_code: int | None,
    error_message: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_api_keys
        SET last_status_code = ?,
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status_code, error_message[:500], key_id),
    )
    conn.commit()


def mark_provider_api_key_invalid(
    conn: sqlite3.Connection,
    key_id: int,
    status_code: int | None,
    error_message: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_api_keys
        SET enabled = 0,
            is_invalid = 1,
            last_status_code = ?,
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status_code, error_message[:500], key_id),
    )
    conn.commit()


def mark_provider_api_key_exhausted(
    conn: sqlite3.Connection,
    key_id: int,
    status_code: int | None,
    error_message: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_api_keys
        SET used_quota = total_quota,
            last_status_code = ?,
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status_code, error_message[:500], key_id),
    )
    conn.commit()


def set_provider_api_key_usage(
    conn: sqlite3.Connection,
    key_id: int,
    used_quota: int,
    total_quota: int | None = None,
    synced: bool = False,
) -> None:
    normalized_used = max(int(used_quota), 0)
    updates: dict[str, Any] = {
        "used_quota": normalized_used,
        "last_error": None,
        "last_status_code": None,
    }
    if total_quota is not None:
        normalized_total = max(int(total_quota), 1)
        updates["total_quota"] = normalized_total
        updates["used_quota"] = min(normalized_used, normalized_total)
    if synced:
        updates["last_synced_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    assignments = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(
        f"""
        UPDATE provider_api_keys
        SET {assignments},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (*updates.values(), key_id),
    )
    conn.commit()


def build_search_cache_key(provider: str, endpoint: str, group_id: int, raw_query: str, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(provider.encode("utf-8"))
    digest.update(b"\0")
    digest.update(endpoint.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(group_id).encode("utf-8"))
    digest.update(b"\0")
    digest.update(raw_query.encode("utf-8"))
    digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


def get_search_cache(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM search_cache
        WHERE cache_key = ?
          AND expires_at > ?
        """,
        (cache_key, datetime.now(timezone.utc).isoformat(timespec="microseconds")),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE search_cache SET hit_count = hit_count + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (row["id"],),
    )
    conn.commit()
    return dict(row)


def store_search_cache(
    conn: sqlite3.Connection,
    cache_key: str,
    provider: str,
    endpoint: str,
    request_body: bytes,
    response_body: bytes,
    status_code: int,
    content_type: str,
    ttl_seconds: int = 43200,
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    conn.execute(
        """
        INSERT INTO search_cache (
            cache_key, provider, endpoint, request_body, response_body,
            status_code, content_type, expires_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            response_body = excluded.response_body,
            status_code = excluded.status_code,
            content_type = excluded.content_type,
            expires_at = excluded.expires_at,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            cache_key,
            provider,
            endpoint,
            request_body.decode("utf-8", "replace"),
            response_body.decode("utf-8", "replace"),
            status_code,
            content_type,
            expires_at.isoformat(timespec="microseconds"),
        ),
    )
    conn.commit()


def _setting_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _setting_int(value: str | None, default: int, minimum: int) -> int:
    if value is None:
        return default
    try:
        return max(int(value), minimum)
    except ValueError:
        return default


def get_cache_settings(
    conn: sqlite3.Connection,
    default_enabled: bool = True,
    default_ttl_seconds: int = 43200,
    default_max_rows: int = 10000,
) -> dict[str, Any]:
    return {
        "enabled": _setting_bool(get_setting(conn, "search_cache_enabled"), default_enabled),
        "ttl_seconds": _setting_int(get_setting(conn, "search_cache_ttl_seconds"), default_ttl_seconds, 1),
        "max_rows": _setting_int(get_setting(conn, "search_cache_max_rows"), default_max_rows, 1),
    }


def set_cache_settings(conn: sqlite3.Connection, enabled: bool, ttl_seconds: int, max_rows: int) -> None:
    set_setting(conn, "search_cache_enabled", "true" if enabled else "false")
    set_setting(conn, "search_cache_ttl_seconds", str(max(int(ttl_seconds), 1)))
    set_setting(conn, "search_cache_max_rows", str(max(int(max_rows), 1)))


def search_cache_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN expires_at > ? THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN expires_at <= ? THEN 1 ELSE 0 END) AS expired,
            COALESCE(SUM(hit_count), 0) AS total_hits,
            COALESCE(SUM(LENGTH(request_body) + LENGTH(response_body)), 0) AS approx_bytes
        FROM search_cache
        """,
        (now, now),
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "active": int(row["active"] or 0),
        "expired": int(row["expired"] or 0),
        "total_hits": int(row["total_hits"] or 0),
        "approx_bytes": int(row["approx_bytes"] or 0),
    }


def list_search_cache_entries(
    conn: sqlite3.Connection,
    provider: str | None = None,
    status: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    clauses = []
    params: list[Any] = [now]
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if status == "active":
        clauses.append("expires_at > ?")
        params.append(now)
    elif status == "expired":
        clauses.append("expires_at <= ?")
        params.append(now)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([max(int(limit), 1), max(int(offset), 0)])
    rows = conn.execute(
        f"""
        SELECT
            id,
            cache_key,
            provider,
            endpoint,
            status_code,
            content_type,
            hit_count,
            expires_at,
            created_at,
            updated_at,
            LENGTH(request_body) AS request_bytes,
            LENGTH(response_body) AS response_bytes,
            CASE WHEN expires_at <= ? THEN 1 ELSE 0 END AS is_expired
        FROM search_cache
        {where}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def count_search_cache_entries(conn: sqlite3.Connection, provider: str | None = None, status: str = "all") -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    clauses = []
    params: list[Any] = []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if status == "active":
        clauses.append("expires_at > ?")
        params.append(now)
    elif status == "expired":
        clauses.append("expires_at <= ?")
        params.append(now)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    row = conn.execute(f"SELECT COUNT(*) AS count FROM search_cache {where}", tuple(params)).fetchone()
    return int(row["count"] or 0)


def delete_search_cache(conn: sqlite3.Connection, cache_id: int) -> bool:
    cursor = conn.execute("DELETE FROM search_cache WHERE id = ?", (cache_id,))
    conn.commit()
    return cursor.rowcount > 0


def prune_expired_search_cache(conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    cursor = conn.execute("DELETE FROM search_cache WHERE expires_at <= ?", (now,))
    conn.commit()
    return int(cursor.rowcount)


def clear_search_cache(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("DELETE FROM search_cache")
    conn.commit()
    return int(cursor.rowcount)


def enforce_search_cache_max_rows(conn: sqlite3.Connection, max_rows: int) -> int:
    removed = prune_expired_search_cache(conn)
    normalized_max_rows = max(int(max_rows), 1)
    row = conn.execute("SELECT COUNT(*) AS count FROM search_cache").fetchone()
    overflow = int(row["count"] or 0) - normalized_max_rows
    if overflow <= 0:
        return removed
    cursor = conn.execute(
        """
        DELETE FROM search_cache
        WHERE id IN (
            SELECT id
            FROM search_cache
            ORDER BY id ASC
            LIMIT ?
        )
        """,
        (overflow,),
    )
    conn.commit()
    return removed + int(cursor.rowcount)


def list_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM groups ORDER BY platform, id").fetchall()]


def get_group(conn: sqlite3.Connection, group_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone())


def get_default_group_id(conn: sqlite3.Connection, platform: str = "exa") -> int:
    name = DEFAULT_GROUP_NAMES.get(platform, f"{platform}-default")
    row = conn.execute("SELECT id FROM groups WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO groups (name, platform, enabled) VALUES (?, ?, 1)",
        (name, platform),
    )
    conn.commit()
    return int(cursor.lastrowid)


def create_group(
    conn: sqlite3.Connection,
    name: str,
    platform: str = "exa",
    enabled: bool = True,
    socks5_proxy: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO groups (name, platform, enabled, socks5_proxy)
        VALUES (?, ?, ?, ?)
        """,
        (name.strip(), platform, 1 if enabled else 0, socks5_proxy.strip() if socks5_proxy else None),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_group(
    conn: sqlite3.Connection,
    group_id: int,
    name: str,
    enabled: bool,
    socks5_proxy: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE groups
        SET name = ?, enabled = ?, socks5_proxy = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (name.strip(), 1 if enabled else 0, socks5_proxy.strip() if socks5_proxy else None, group_id),
    )
    conn.commit()


def set_group_enabled(conn: sqlite3.Connection, group_id: int, enabled: bool) -> None:
    conn.execute(
        "UPDATE groups SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (1 if enabled else 0, group_id),
    )
    conn.commit()


def set_relay_key_groups(
    conn: sqlite3.Connection,
    relay_key_id: int,
    provider: str,
    group_ids: list[int] | tuple[int, ...] | None,
) -> None:
    normalized = normalize_group_ids(group_ids)
    conn.execute("DELETE FROM relay_key_groups WHERE relay_key_id = ? AND provider = ?", (relay_key_id, provider))
    for priority, group_id in enumerate(normalized):
        conn.execute(
            """
            INSERT INTO relay_key_groups (relay_key_id, provider, group_id, priority)
            VALUES (?, ?, ?, ?)
            """,
            (relay_key_id, provider, group_id, priority),
        )
    first_group_id = normalized[0] if normalized else None
    if provider == "exa":
        conn.execute(
            "UPDATE relay_keys SET group_id = ?, exa_group_id = ? WHERE id = ?",
            (first_group_id, first_group_id, relay_key_id),
        )
    elif provider == "tavily":
        conn.execute(
            "UPDATE relay_keys SET tavily_group_id = ? WHERE id = ?",
            (first_group_id, relay_key_id),
        )
    conn.commit()


def get_relay_key_provider_groups(conn: sqlite3.Connection, relay_key_id: int, provider: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT groups.*, relay_key_groups.priority
            FROM relay_key_groups
            JOIN groups ON groups.id = relay_key_groups.group_id
            WHERE relay_key_groups.relay_key_id = ?
              AND relay_key_groups.provider = ?
              AND groups.platform = ?
            ORDER BY relay_key_groups.priority ASC, groups.id ASC
            """,
            (relay_key_id, provider, provider),
        ).fetchall()
    ]


def group_remaining_quota(conn: sqlite3.Connection, provider: str, group_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(total_quota - used_quota), 0) AS remaining
        FROM provider_api_keys
        WHERE provider_name = ?
          AND group_id = ?
          AND enabled = 1
          AND is_invalid = 0
        """,
        (provider, group_id),
    ).fetchone()
    return int(row["remaining"] or 0)


def count_group_provider_requests_today(conn: sqlite3.Connection, provider: str, group_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM request_logs
        WHERE provider = ?
          AND provider_group_id = ?
          AND date(created_at) = date('now')
        """,
        (provider, group_id),
    ).fetchone()
    return int(row["count"] or 0)


def create_relay_key(
    conn: sqlite3.Connection,
    label: str,
    key_hash: str,
    daily_limit: int | None,
    key_value: str | None = None,
    group_id: int | None = None,
    exa_group_id: int | None = None,
    tavily_group_id: int | None = None,
    exa_group_ids: list[int] | None = None,
    tavily_group_ids: list[int] | None = None,
    assign_default_groups: bool = True,
) -> int:
    assigned_group_id = group_id if group_id is not None else get_default_group_id(conn, "exa")
    if exa_group_ids is None:
        if assign_default_groups and exa_group_id is None:
            exa_group_id = assigned_group_id
        normalized_exa_group_ids = [] if exa_group_id is None else [exa_group_id]
    else:
        normalized_exa_group_ids = normalize_group_ids(exa_group_ids)
    if tavily_group_ids is None:
        if assign_default_groups and tavily_group_id is None:
            tavily_group_id = get_default_group_id(conn, "tavily")
        normalized_tavily_group_ids = [] if tavily_group_id is None else [tavily_group_id]
    else:
        normalized_tavily_group_ids = normalize_group_ids(tavily_group_ids)
    exa_group_id = normalized_exa_group_ids[0] if normalized_exa_group_ids else None
    tavily_group_id = normalized_tavily_group_ids[0] if normalized_tavily_group_ids else None
    assigned_group_id = exa_group_id if group_id is None else group_id
    cursor = conn.execute(
        """
        INSERT INTO relay_keys (label, group_id, exa_group_id, tavily_group_id, key_value, key_hash, daily_limit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (label, assigned_group_id, exa_group_id, tavily_group_id, key_value, key_hash, daily_limit),
    )
    key_id = int(cursor.lastrowid)
    set_relay_key_groups(conn, key_id, "exa", normalized_exa_group_ids)
    set_relay_key_groups(conn, key_id, "tavily", normalized_tavily_group_ids)
    return key_id


def list_relay_keys(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    keys = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                relay_keys.*,
                legacy_groups.name AS group_name,
                exa_groups.name AS exa_group_name,
                tavily_groups.name AS tavily_group_name
            FROM relay_keys
            LEFT JOIN groups AS legacy_groups ON legacy_groups.id = relay_keys.group_id
            LEFT JOIN groups AS exa_groups ON exa_groups.id = relay_keys.exa_group_id
            LEFT JOIN groups AS tavily_groups ON tavily_groups.id = relay_keys.tavily_group_id
            ORDER BY relay_keys.id DESC
            """
        ).fetchall()
    ]
    for key in keys:
        key["exa_groups"] = get_relay_key_provider_groups(conn, int(key["id"]), "exa")
        key["tavily_groups"] = get_relay_key_provider_groups(conn, int(key["id"]), "tavily")
    return keys


def get_relay_key_by_hash(conn: sqlite3.Connection, key_hash: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM relay_keys WHERE key_hash = ?", (key_hash,)).fetchone())


def get_relay_key(conn: sqlite3.Connection, key_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM relay_keys WHERE id = ?", (key_id,)).fetchone())


def set_relay_key_enabled(conn: sqlite3.Connection, key_id: int, enabled: bool) -> None:
    conn.execute("UPDATE relay_keys SET enabled = ? WHERE id = ?", (1 if enabled else 0, key_id))
    conn.commit()


def update_relay_key(
    conn: sqlite3.Connection,
    key_id: int,
    label: str,
    enabled: bool,
    exa_group_id: int | None,
    tavily_group_id: int | None,
    daily_limit: int | None,
    exa_group_ids: list[int] | None = None,
    tavily_group_ids: list[int] | None = None,
) -> None:
    normalized_exa_group_ids = normalize_group_ids(exa_group_ids) if exa_group_ids is not None else ([] if exa_group_id is None else [exa_group_id])
    normalized_tavily_group_ids = normalize_group_ids(tavily_group_ids) if tavily_group_ids is not None else ([] if tavily_group_id is None else [tavily_group_id])
    exa_group_id = normalized_exa_group_ids[0] if normalized_exa_group_ids else None
    tavily_group_id = normalized_tavily_group_ids[0] if normalized_tavily_group_ids else None
    conn.execute(
        """
        UPDATE relay_keys
        SET label = ?,
            enabled = ?,
            group_id = ?,
            exa_group_id = ?,
            tavily_group_id = ?,
            daily_limit = ?
        WHERE id = ?
        """,
        (label.strip(), 1 if enabled else 0, exa_group_id, exa_group_id, tavily_group_id, daily_limit, key_id),
    )
    set_relay_key_groups(conn, key_id, "exa", normalized_exa_group_ids)
    set_relay_key_groups(conn, key_id, "tavily", normalized_tavily_group_ids)


def delete_relay_key(conn: sqlite3.Connection, key_id: int) -> None:
    conn.execute("UPDATE request_logs SET relay_key_id = NULL WHERE relay_key_id = ?", (key_id,))
    conn.execute("DELETE FROM relay_key_groups WHERE relay_key_id = ?", (key_id,))
    conn.execute("DELETE FROM relay_keys WHERE id = ?", (key_id,))
    conn.commit()


def count_key_requests_today(conn: sqlite3.Connection, relay_key_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM request_logs
        WHERE relay_key_id = ?
          AND date(created_at) = date('now')
        """,
        (relay_key_id,),
    ).fetchone()
    return int(row["count"])


def count_group_requests_today(conn: sqlite3.Connection, group_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM request_logs
        WHERE request_logs.provider_group_id = ?
          AND date(request_logs.created_at) = date('now')
        """,
        (group_id,),
    ).fetchone()
    return int(row["count"])


def record_request_log(
    conn: sqlite3.Connection,
    provider: str,
    endpoint: str,
    relay_key_id: int | None,
    status_code: int,
    duration_ms: int,
    request_bytes: int,
    response_bytes: int,
    error_code: str | None,
    error_message: str | None,
    provider_group_id: int | None = None,
    provider_group_name: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO request_logs (
            provider, endpoint, relay_key_id, status_code, duration_ms,
            request_bytes, response_bytes, provider_group_id, provider_group_name,
            error_code, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            provider,
            endpoint,
            relay_key_id,
            status_code,
            duration_ms,
            request_bytes,
            response_bytes,
            provider_group_id,
            provider_group_name,
            error_code,
            error_message[:500] if error_message else None,
        ),
    )
    conn.commit()


def recent_request_logs(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT request_logs.*, relay_keys.label AS relay_key_label
            FROM request_logs
            LEFT JOIN relay_keys ON relay_keys.id = request_logs.relay_key_id
            ORDER BY request_logs.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]


def _request_log_filters(
    provider: str | None = None,
    status: str = "all",
    relay_key_id: int | None = None,
    endpoint: str | None = None,
    q: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if provider:
        clauses.append("request_logs.provider = ?")
        params.append(provider)
    if status == "success":
        clauses.append("request_logs.status_code BETWEEN 200 AND 399")
    elif status == "error":
        clauses.append("request_logs.status_code >= 400")
    elif status == "client_error":
        clauses.append("request_logs.status_code BETWEEN 400 AND 499")
    elif status == "server_error":
        clauses.append("request_logs.status_code >= 500")
    if relay_key_id is not None:
        clauses.append("request_logs.relay_key_id = ?")
        params.append(relay_key_id)
    if endpoint:
        clauses.append("request_logs.endpoint = ?")
        params.append(endpoint)
    if created_from:
        clauses.append("request_logs.created_at >= ?")
        params.append(created_from)
    if created_to:
        clauses.append("request_logs.created_at <= ?")
        params.append(created_to)
    if q:
        like = f"%{q.strip()}%"
        clauses.append(
            """
            (
                request_logs.provider LIKE ?
                OR request_logs.endpoint LIKE ?
                OR request_logs.error_code LIKE ?
                OR request_logs.error_message LIKE ?
                OR relay_keys.label LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_request_logs(
    conn: sqlite3.Connection,
    provider: str | None = None,
    status: str = "all",
    relay_key_id: int | None = None,
    endpoint: str | None = None,
    q: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where, params = _request_log_filters(provider, status, relay_key_id, endpoint, q, created_from, created_to)
    params.extend([max(int(limit), 1), max(int(offset), 0)])
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT request_logs.*, relay_keys.label AS relay_key_label
            FROM request_logs
            LEFT JOIN relay_keys ON relay_keys.id = request_logs.relay_key_id
            {where}
            ORDER BY request_logs.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        ).fetchall()
    ]


def count_request_logs(
    conn: sqlite3.Connection,
    provider: str | None = None,
    status: str = "all",
    relay_key_id: int | None = None,
    endpoint: str | None = None,
    q: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> int:
    where, params = _request_log_filters(provider, status, relay_key_id, endpoint, q, created_from, created_to)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM request_logs
        LEFT JOIN relay_keys ON relay_keys.id = request_logs.relay_key_id
        {where}
        """,
        tuple(params),
    ).fetchone()
    return int(row["count"] or 0)


def get_request_log(conn: sqlite3.Connection, log_id: int) -> dict[str, Any] | None:
    return row_to_dict(
        conn.execute(
            """
            SELECT request_logs.*, relay_keys.label AS relay_key_label
            FROM request_logs
            LEFT JOIN relay_keys ON relay_keys.id = request_logs.relay_key_id
            WHERE request_logs.id = ?
            """,
            (log_id,),
        ).fetchone()
    )


def dashboard_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    today = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status_code BETWEEN 200 AND 399 THEN 1 ELSE 0 END) AS success,
          COALESCE(AVG(duration_ms), 0) AS avg_duration
        FROM request_logs
        WHERE date(created_at) = date('now')
        """
    ).fetchone()
    by_provider = [
        dict(row)
        for row in conn.execute(
            """
            SELECT provider, COUNT(*) AS count
            FROM request_logs
            WHERE date(created_at) = date('now')
            GROUP BY provider
            ORDER BY count DESC
            """
        ).fetchall()
    ]
    total = int(today["total"] or 0)
    success = int(today["success"] or 0)
    return {
        "requests_today": total,
        "success_rate": round((success / total) * 100, 1) if total else 0,
        "avg_duration_ms": round(float(today["avg_duration"] or 0), 1),
        "by_provider": by_provider,
    }
