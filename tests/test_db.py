from app.db import connect, init_db
from app.providers import PROVIDERS
from app.repositories import (
    create_group,
    create_provider,
    create_provider_api_key,
    create_relay_key,
    clear_search_cache,
    count_group_provider_requests_today,
    count_key_requests_today,
    delete_search_cache,
    enforce_search_cache_max_rows,
    get_candidate_provider_api_keys,
    get_cache_settings,
    get_default_group_id,
    get_provider,
    get_relay_key_provider_groups,
    get_request_log,
    get_relay_key_by_hash,
    list_request_logs,
    list_groups,
    list_provider_api_keys,
    list_providers,
    list_relay_keys,
    list_search_cache_entries,
    prune_expired_search_cache,
    prune_request_logs,
    recent_request_logs,
    record_request_log,
    search_cache_stats,
    count_request_logs,
    set_provider_api_key_usage,
    set_cache_settings,
    set_relay_key_groups,
    store_search_cache,
    update_group,
    update_provider_api_key,
    update_relay_key,
)


def test_init_db_creates_default_providers(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        providers = list_providers(conn)

    assert [provider["name"] for provider in providers] == ["brave", "exa", "jina", "tavily"]
    assert all(provider["enabled"] == 0 for provider in providers)


def test_init_db_creates_provider_default_groups(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        groups = list_groups(conn)

    assert {(group["name"], group["platform"]) for group in groups} == {
        ("default", "exa"),
        ("tavily-default", "tavily"),
        ("brave-default", "brave"),
        ("jina-default", "jina"),
    }


def test_providers_table_matches_registry_after_init(tmp_path):
    """Every registry provider is seeded into the providers table by init_db.

    The registry (app/providers.py) is the authority for routes/auth; the DB
    providers table is the authority for enablement/keys. They must stay in
    sync — this test fails when a registry provider is added without a seed
    row (or a seed row exists for a provider the registry no longer knows).
    """
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        seeded = {provider["name"] for provider in list_providers(conn)}

    assert seeded == set(PROVIDERS.keys())


def test_new_provider_requires_no_relay_keys_schema_change(tmp_path):
    """Adding a 5th provider must not require relay_keys schema changes.

    Proves the acceptance criterion: a brand-new provider (e.g. "firecrawl")
    is bound purely through relay_key_groups — no brave/jina-style group_id
    columns, no new columns at all.
    """
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        columns_before = {
            row["name"] for row in conn.execute("PRAGMA table_info(relay_keys)").fetchall()
        }

        create_provider(conn, "firecrawl", api_key="", enabled=False)
        firecrawl_group_id = create_group(
            conn, name="firecrawl-pool", platform="firecrawl", enabled=True
        )
        key_id = create_relay_key(
            conn,
            label="firecrawl-client",
            key_hash="hash-firecrawl",
            daily_limit=None,
            provider_groups={"firecrawl": [firecrawl_group_id]},
            assign_default_groups=False,
        )
        bound = get_relay_key_provider_groups(conn, key_id, "firecrawl")
        columns_after = {
            row["name"] for row in conn.execute("PRAGMA table_info(relay_keys)").fetchall()
        }

    assert [group["id"] for group in bound] == [firecrawl_group_id]
    assert columns_before == columns_after


def test_init_db_migrates_grouped_schema_without_platform_columns(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE providers (
                name TEXT PRIMARY KEY,
                api_key TEXT,
                base_url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE provider_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_name TEXT NOT NULL,
                label TEXT NOT NULL,
                api_key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                use_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(provider_name) REFERENCES providers(name) ON DELETE CASCADE
            );

            CREATE TABLE groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                daily_limit INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE relay_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                group_id INTEGER,
                key_hash TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                daily_limit INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(group_id) REFERENCES groups(id)
            );
            """
        )
        conn.execute(
            "INSERT INTO providers (name, api_key, base_url, enabled) VALUES (?, ?, ?, 1)",
            ("exa", "legacy-exa-key", "https://api.exa.ai"),
        )
        conn.execute("INSERT INTO groups (name, enabled) VALUES (?, 1)", ("default",))
        init_db(conn)

        group_columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)").fetchall()}
        provider_key_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(provider_api_keys)").fetchall()
        }
        relay_key_columns = {row["name"] for row in conn.execute("PRAGMA table_info(relay_keys)").fetchall()}
        groups = list_groups(conn)
        provider_keys = list_provider_api_keys(conn, "exa")

    assert "platform" in group_columns
    assert "group_id" in provider_key_columns
    assert "total_quota" in provider_key_columns
    assert "used_quota" in provider_key_columns
    assert "is_invalid" in provider_key_columns
    assert "last_error" in provider_key_columns
    assert "last_status_code" in provider_key_columns
    assert "last_synced_at" in provider_key_columns
    assert "exa_group_id" in relay_key_columns
    assert "tavily_group_id" in relay_key_columns
    assert "key_value" in relay_key_columns
    assert "key_fingerprint" in relay_key_columns
    assert {(group["name"], group["platform"]) for group in groups} == {
        ("default", "exa"),
        ("tavily-default", "tavily"),
        ("brave-default", "brave"),
        ("jina-default", "jina"),
    }
    assert provider_keys[0]["api_key"] == "legacy-exa-key"
    assert provider_keys[0]["group_name"] == "default"


def test_init_db_creates_multi_group_mapping_and_proxy_column(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        group_columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)").fetchall()}
        mapping_columns = {row["name"] for row in conn.execute("PRAGMA table_info(relay_key_groups)").fetchall()}

    assert "socks5_proxy" in group_columns
    assert {"relay_key_id", "provider", "group_id", "priority", "created_at"} <= mapping_columns


def test_init_db_migrates_legacy_relay_group_columns_to_mapping(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        exa_group_id = create_group(conn, name="legacy-exa", platform="exa", enabled=True)
        tavily_group_id = create_group(conn, name="legacy-tavily", platform="tavily", enabled=True)
        key_id = create_relay_key(
            conn,
            label="legacy-client",
            key_hash="legacy-hash",
            daily_limit=None,
            exa_group_id=exa_group_id,
            tavily_group_id=tavily_group_id,
            assign_default_groups=False,
        )
        conn.execute("DELETE FROM relay_key_groups WHERE relay_key_id = ?", (key_id,))
        conn.commit()

        init_db(conn)
        exa_groups = get_relay_key_provider_groups(conn, key_id, "exa")
        tavily_groups = get_relay_key_provider_groups(conn, key_id, "tavily")

    assert [group["id"] for group in exa_groups] == [exa_group_id]
    assert [group["id"] for group in tavily_groups] == [tavily_group_id]


def test_group_proxy_and_relay_key_group_mapping_round_trip(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        first_group = create_group(
            conn,
            name="exa-proxy-a",
            platform="exa",
            enabled=True,
            socks5_proxy="socks5://127.0.0.1:1080",
        )
        second_group = create_group(conn, name="exa-proxy-b", platform="exa", enabled=True)
        key_id = create_relay_key(
            conn,
            label="multi-client",
            key_hash="hash-multi",
            daily_limit=None,
            exa_group_ids=[first_group, second_group],
            tavily_group_ids=[],
            assign_default_groups=False,
        )
        update_group(
            conn,
            first_group,
            name="exa-proxy-a-renamed",
            enabled=False,
            socks5_proxy="socks5h://proxy.example.com:1080",
        )
        set_relay_key_groups(conn, key_id, "exa", [second_group, first_group])
        groups = list_groups(conn)
        mapped = get_relay_key_provider_groups(conn, key_id, "exa")
        relay_key = next(key for key in list_relay_keys(conn) if key["id"] == key_id)

    updated = next(group for group in groups if group["id"] == first_group)
    assert updated["socks5_proxy"] == "socks5h://proxy.example.com:1080"
    assert updated["enabled"] == 0
    assert [group["id"] for group in mapped] == [second_group, first_group]
    assert relay_key["exa_group_id"] == second_group
    assert [group["id"] for group in relay_key["exa_groups"]] == [second_group, first_group]
    assert relay_key["tavily_groups"] == []


def test_relay_key_groups_supports_arbitrary_provider(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        brave_group_id = create_group(conn, name="brave-pool", platform="brave", enabled=True)
        key_id = create_relay_key(
            conn,
            label="brave-client",
            key_hash="hash-brave",
            daily_limit=None,
            provider_groups={"brave": [brave_group_id]},
            assign_default_groups=False,
        )
        brave_groups = get_relay_key_provider_groups(conn, key_id, "brave")
        relay_key = get_relay_key_by_hash(conn, "hash-brave")

    assert [group["id"] for group in brave_groups] == [brave_group_id]
    # No legacy relay_keys columns are written for a non-exa/tavily provider.
    assert relay_key["group_id"] is None
    assert relay_key["exa_group_id"] is None
    assert relay_key["tavily_group_id"] is None


def test_relay_key_groups_supports_brave(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        # Seeded default group for the brave provider resolves without creating one.
        brave_default = get_default_group_id(conn, "brave")
        key_id = create_relay_key(
            conn,
            label="brave-client",
            key_hash="hash-brave-seeded",
            daily_limit=None,
            provider_groups={"brave": [brave_default]},
            assign_default_groups=False,
        )
        brave_groups = get_relay_key_provider_groups(conn, key_id, "brave")
        relay_key = get_relay_key_by_hash(conn, "hash-brave-seeded")

    assert [group["id"] for group in brave_groups] == [brave_default]
    assert [group["name"] for group in brave_groups] == ["brave-default"]
    # Brave goes through relay_key_groups only — no new relay_keys columns.
    assert relay_key["group_id"] is None
    assert relay_key["exa_group_id"] is None
    assert relay_key["tavily_group_id"] is None


def test_relay_key_groups_supports_jina(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        # Seeded default group for the jina provider resolves without creating one.
        jina_default = get_default_group_id(conn, "jina")
        key_id = create_relay_key(
            conn,
            label="jina-client",
            key_hash="hash-jina-seeded",
            daily_limit=None,
            provider_groups={"jina": [jina_default]},
            assign_default_groups=False,
        )
        jina_groups = get_relay_key_provider_groups(conn, key_id, "jina")
        relay_key = get_relay_key_by_hash(conn, "hash-jina-seeded")

    assert [group["id"] for group in jina_groups] == [jina_default]
    assert [group["name"] for group in jina_groups] == ["jina-default"]
    # Jina goes through relay_key_groups only — no new relay_keys columns.
    assert relay_key["group_id"] is None
    assert relay_key["exa_group_id"] is None
    assert relay_key["tavily_group_id"] is None


def test_create_relay_key_with_provider_groups_dict(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        exa_group_id = create_group(conn, name="exa-pool", platform="exa", enabled=True)
        tavily_group_id = create_group(conn, name="tavily-pool", platform="tavily", enabled=True)
        brave_group_id = create_group(conn, name="brave-pool", platform="brave", enabled=True)
        key_id = create_relay_key(
            conn,
            label="multi-provider",
            key_hash="hash-multi-provider",
            daily_limit=None,
            provider_groups={
                "exa": [exa_group_id],
                "tavily": [tavily_group_id],
                "brave": [brave_group_id],
            },
            assign_default_groups=False,
        )
        exa_groups = get_relay_key_provider_groups(conn, key_id, "exa")
        tavily_groups = get_relay_key_provider_groups(conn, key_id, "tavily")
        brave_groups = get_relay_key_provider_groups(conn, key_id, "brave")
        relay_key = get_relay_key_by_hash(conn, "hash-multi-provider")

    assert [group["id"] for group in exa_groups] == [exa_group_id]
    assert [group["id"] for group in tavily_groups] == [tavily_group_id]
    assert [group["id"] for group in brave_groups] == [brave_group_id]
    # Legacy columns mirrored only for exa/tavily.
    assert relay_key["group_id"] == exa_group_id
    assert relay_key["exa_group_id"] == exa_group_id
    assert relay_key["tavily_group_id"] == tavily_group_id


def test_legacy_exa_tavily_data_still_works_after_generalization(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        exa_group_id = create_group(conn, name="exa-legacy", platform="exa", enabled=True)
        tavily_group_id = create_group(conn, name="tavily-legacy", platform="tavily", enabled=True)
        key_id = create_relay_key(
            conn,
            label="legacy-client",
            key_hash="hash-legacy",
            daily_limit=None,
            exa_group_id=exa_group_id,
            tavily_group_id=tavily_group_id,
            assign_default_groups=False,
        )
        exa_groups = get_relay_key_provider_groups(conn, key_id, "exa")
        tavily_groups = get_relay_key_provider_groups(conn, key_id, "tavily")
        relay_key = get_relay_key_by_hash(conn, "hash-legacy")

    assert [group["id"] for group in exa_groups] == [exa_group_id]
    assert [group["id"] for group in tavily_groups] == [tavily_group_id]
    assert relay_key["exa_group_id"] == exa_group_id
    assert relay_key["tavily_group_id"] == tavily_group_id


def test_list_relay_keys_exposes_groups_by_provider(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        exa_group_id = create_group(conn, name="exa-list", platform="exa", enabled=True)
        brave_group_id = create_group(conn, name="brave-list", platform="brave", enabled=True)
        key_id = create_relay_key(
            conn,
            label="listed",
            key_hash="hash-listed",
            daily_limit=None,
            provider_groups={"exa": [exa_group_id], "brave": [brave_group_id]},
            assign_default_groups=False,
        )
        relay_key = next(key for key in list_relay_keys(conn) if key["id"] == key_id)

    assert [group["id"] for group in relay_key["groups_by_provider"]["exa"]] == [exa_group_id]
    assert [group["id"] for group in relay_key["groups_by_provider"]["brave"]] == [brave_group_id]
    # Legacy per-provider keys still populated from the canonical table.
    assert [group["id"] for group in relay_key["exa_groups"]] == [exa_group_id]
    assert relay_key["tavily_groups"] == []


def test_relay_key_and_log_round_trip(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        exa_group_id = create_group(conn, name="exa-vip", platform="exa", enabled=True)
        key_id = create_relay_key(
            conn,
            label="agent",
            key_hash="hash-1",
            daily_limit=100,
            exa_group_id=exa_group_id,
            tavily_group_id=None,
            assign_default_groups=False,
        )
        key = get_relay_key_by_hash(conn, "hash-1")
        record_request_log(
            conn,
            provider="exa",
            endpoint="/search",
            relay_key_id=key_id,
            status_code=200,
            duration_ms=123,
            request_bytes=20,
            response_bytes=30,
            error_code=None,
            error_message=None,
        )
        logs = recent_request_logs(conn, limit=10)

    assert key["label"] == "agent"
    assert key["daily_limit"] == 100
    assert key["exa_group_id"] == exa_group_id
    assert key["tavily_group_id"] is None
    assert logs[0]["provider"] == "exa"
    assert logs[0]["status_code"] == 200


def test_request_log_records_internal_upstream_key_id(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        record_request_log(
            conn,
            provider="exa",
            endpoint="/search",
            relay_key_id=None,
            status_code=200,
            duration_ms=10,
            request_bytes=100,
            response_bytes=200,
            error_code=None,
            error_message=None,
            provider_group_id=3,
            provider_group_name="exa-vip",
            provider_key_id=7,
        )
        logs = recent_request_logs(conn, limit=10)

    assert logs[0]["provider_key_id"] == 7
    assert logs[0]["provider_group_id"] == 3


def test_request_log_provider_key_id_defaults_to_null(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        record_request_log(
            conn,
            provider="exa",
            endpoint="/search",
            relay_key_id=None,
            status_code=200,
            duration_ms=10,
            request_bytes=100,
            response_bytes=200,
            error_code=None,
            error_message=None,
        )
        logs = recent_request_logs(conn, limit=10)

    assert logs[0]["provider_key_id"] is None


def test_init_db_adds_provider_key_id_column_to_request_logs(tmp_path):
    # A database created by an older schema (no provider_key_id) must gain the
    # column additively, with legacy rows backfilled to NULL (never a key value).
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                provider TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                relay_key_id INTEGER,
                status_code INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                request_bytes INTEGER NOT NULL,
                response_bytes INTEGER NOT NULL,
                error_code TEXT,
                error_message TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO request_logs (provider, endpoint, status_code, duration_ms, request_bytes, response_bytes) "
            "VALUES ('exa', '/search', 200, 1, 1, 1)"
        )
        init_db(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(request_logs)").fetchall()}
        row = conn.execute("SELECT provider_key_id FROM request_logs LIMIT 1").fetchone()

    assert "provider_key_id" in columns
    assert row["provider_key_id"] is None


def test_request_logs_can_be_filtered_paginated_and_loaded_by_id(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        key_id = create_relay_key(
            conn,
            label="agent",
            key_hash="hash-logs",
            daily_limit=None,
        )
        record_request_log(
            conn,
            provider="exa",
            endpoint="/search",
            relay_key_id=key_id,
            status_code=200,
            duration_ms=40,
            request_bytes=10,
            response_bytes=20,
            error_code=None,
            error_message=None,
        )
        record_request_log(
            conn,
            provider="exa",
            endpoint="/search",
            relay_key_id=key_id,
            status_code=504,
            duration_ms=1200,
            request_bytes=11,
            response_bytes=0,
            error_code="upstream_timeout",
            error_message="upstream timeout while searching",
        )
        record_request_log(
            conn,
            provider="tavily",
            endpoint="/extract",
            relay_key_id=None,
            status_code=429,
            duration_ms=80,
            request_bytes=12,
            response_bytes=30,
            error_code="upstream_error",
            error_message="rate limited",
        )

        error_logs = list_request_logs(conn, provider="exa", status="error", q="timeout", limit=10, offset=0)
        total_errors = count_request_logs(conn, provider="exa", status="error", q="timeout")
        success_logs = list_request_logs(conn, status="success", limit=10, offset=0)
        paged_logs = list_request_logs(conn, status="all", limit=1, offset=1)
        loaded = get_request_log(conn, error_logs[0]["id"])

    assert total_errors == 1
    assert len(error_logs) == 1
    assert error_logs[0]["provider"] == "exa"
    assert error_logs[0]["status_code"] == 504
    assert error_logs[0]["relay_key_label"] == "agent"
    assert error_logs[0]["error_message"] == "upstream timeout while searching"
    assert len(success_logs) == 1
    assert success_logs[0]["status_code"] == 200
    assert len(paged_logs) == 1
    assert loaded is not None
    assert loaded["id"] == error_logs[0]["id"]


def test_provider_upsert_updates_api_key(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        create_provider(conn, name="exa", api_key="exa-key", enabled=True)
        provider = get_provider(conn, "exa")

    assert provider["api_key"] == "exa-key"
    assert provider["enabled"] == 1


def test_provider_api_key_records_group(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        group_id = create_group(conn, name="exa-special", platform="exa", enabled=True)
        key_id = create_provider_api_key(
            conn,
            provider_name="exa",
            label="primary",
            api_key="exa-secret",
            enabled=True,
            group_id=group_id,
        )
        keys = list_provider_api_keys(conn, "exa")

    created = next(key for key in keys if key["id"] == key_id)
    assert created["group_id"] == group_id
    assert created["group_name"] == "exa-special"


def test_group_provider_key_and_relay_key_updates(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        exa_group_id = create_group(conn, name="exa-special", platform="exa", enabled=True)
        tavily_group_id = create_group(conn, name="tavily-special", platform="tavily", enabled=True)
        replacement_exa_group_id = create_group(conn, name="exa-replacement", platform="exa", enabled=True)
        provider_key_id = create_provider_api_key(
            conn,
            provider_name="exa",
            label="primary",
            api_key="exa-secret",
            enabled=True,
            group_id=exa_group_id,
            total_quota=100,
        )
        relay_key_id = create_relay_key(
            conn,
            label="agent",
            key_hash="hash-1",
            key_value="relay-secret",
            daily_limit=50,
            exa_group_id=exa_group_id,
            tavily_group_id=tavily_group_id,
            assign_default_groups=False,
        )

        update_group(conn, exa_group_id, name="exa-renamed", enabled=False)
        update_provider_api_key(
            conn,
            provider_name="exa",
            key_id=provider_key_id,
            label="primary-renamed",
            api_key=None,
            enabled=False,
            group_id=replacement_exa_group_id,
            total_quota=250,
        )
        update_relay_key(
            conn,
            key_id=relay_key_id,
            label="agent-renamed",
            enabled=False,
            exa_group_id=replacement_exa_group_id,
            tavily_group_id=None,
            daily_limit=None,
        )

        groups = list_groups(conn)
        provider_key = next(key for key in list_provider_api_keys(conn, "exa") if key["id"] == provider_key_id)
        relay_key = next(key for key in list_relay_keys(conn) if key["id"] == relay_key_id)

    updated_group = next(group for group in groups if group["id"] == exa_group_id)
    assert updated_group["name"] == "exa-renamed"
    assert updated_group["enabled"] == 0
    assert provider_key["label"] == "primary-renamed"
    assert provider_key["api_key"] == "exa-secret"
    assert provider_key["enabled"] == 0
    assert provider_key["group_id"] == replacement_exa_group_id
    assert provider_key["total_quota"] == 250
    assert relay_key["label"] == "agent-renamed"
    assert relay_key["enabled"] == 0
    assert relay_key["exa_group_id"] == replacement_exa_group_id
    assert relay_key["tavily_group_id"] is None
    assert relay_key["daily_limit"] is None


def test_provider_api_key_candidates_use_remaining_quota(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        create_provider(conn, name="exa", api_key="", enabled=True)
        group_id = create_group(conn, name="exa-quota", platform="exa", enabled=True)
        low_id = create_provider_api_key(
            conn,
            provider_name="exa",
            label="low",
            api_key="exa-low",
            enabled=True,
            group_id=group_id,
            total_quota=10,
        )
        high_id = create_provider_api_key(
            conn,
            provider_name="exa",
            label="high",
            api_key="exa-high",
            enabled=True,
            group_id=group_id,
            total_quota=100,
        )
        set_provider_api_key_usage(conn, low_id, used_quota=1, total_quota=10)
        set_provider_api_key_usage(conn, high_id, used_quota=90, total_quota=100)
        candidates = get_candidate_provider_api_keys(conn, "exa", group_id)

    assert [candidate["id"] for candidate in candidates] == [high_id, low_id]


def test_cache_settings_round_trip(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)

        defaults = get_cache_settings(conn, default_enabled=False, default_ttl_seconds=60, default_max_rows=5)
        set_cache_settings(conn, enabled=True, ttl_seconds=120, max_rows=25)
        updated = get_cache_settings(conn, default_enabled=False, default_ttl_seconds=60, default_max_rows=5)

    assert defaults == {"enabled": False, "ttl_seconds": 60, "max_rows": 5}
    assert updated == {"enabled": True, "ttl_seconds": 120, "max_rows": 25}


def test_search_cache_management_round_trip(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        store_search_cache(
            conn,
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
            conn,
            "expired-key",
            "tavily",
            "search",
            b'{"query":"expired"}',
            b'{"results":["expired"]}',
            200,
            "application/json",
            ttl_seconds=-60,
        )

        stats_before = search_cache_stats(conn)
        entries_before = list_search_cache_entries(conn, limit=10, offset=0)
        exa_entries = list_search_cache_entries(conn, provider="exa", limit=10, offset=0)
        active_entry = next(entry for entry in entries_before if entry["cache_key"] == "active-key")
        pruned = prune_expired_search_cache(conn)
        deleted = delete_search_cache(conn, active_entry["id"])
        cleared = clear_search_cache(conn)
        stats_after = search_cache_stats(conn)

    assert stats_before["total"] == 2
    assert stats_before["active"] == 1
    assert stats_before["expired"] == 1
    assert stats_before["total_hits"] == 0
    assert stats_before["approx_bytes"] > 0
    assert len(entries_before) == 2
    assert entries_before[0]["response_bytes"] > 0
    assert len(exa_entries) == 1
    assert exa_entries[0]["provider"] == "exa"
    assert pruned == 1
    assert deleted is True
    assert cleared == 0
    assert stats_after["total"] == 0


def test_search_cache_max_rows_removes_oldest_entries(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        for index in range(3):
            store_search_cache(
                conn,
                f"cache-key-{index}",
                "exa",
                "search",
                f'{{"query":"{index}"}}'.encode("utf-8"),
                f'{{"results":["{index}"]}}'.encode("utf-8"),
                200,
                "application/json",
                ttl_seconds=60,
            )

        removed = enforce_search_cache_max_rows(conn, max_rows=2)
        entries = list_search_cache_entries(conn, limit=10, offset=0)

    assert removed == 1
    assert len(entries) == 2
    assert {entry["cache_key"] for entry in entries} == {"cache-key-1", "cache-key-2"}


def test_init_db_creates_request_log_indexes(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        index_rows = conn.execute("PRAGMA index_list(request_logs)").fetchall()
        index_names = {row["name"] for row in index_rows}

    assert {
        "idx_request_logs_created_at",
        "idx_request_logs_relay_key_id",
        "idx_request_logs_provider_group_created",
        "idx_request_logs_provider_created",
    } <= index_names


def test_daily_count_queries_use_request_log_index(tmp_path):
    # Function-on-column predicates (date(created_at) = date('now')) cannot use
    # an index; the rewrite to created_at >= start-of-day must. Locked here so
    # the routing hot path stays index-backed as the table grows.
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        relay_key_id = create_relay_key(conn, "test", "hash", None)
        group = create_group(conn, "pool-a", platform="exa")
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM request_logs "
            "WHERE provider = 'exa' AND provider_group_id = 1 "
            "AND created_at >= datetime('now', 'start of day')"
        ).fetchall()
        plan_text = " ".join(row["detail"] for row in plan)

    assert "idx_request_logs_provider_group_created" in plan_text


def test_prune_request_logs_removes_old_rows_only(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        relay_key_id = create_relay_key(conn, "test", "hash", None)
        record_request_log(conn, "exa", "/search", relay_key_id, 200, 5, 10, 20, None, None)
        conn.execute(
            "UPDATE request_logs SET created_at = datetime('now', '-40 days') "
            "WHERE id = (SELECT MAX(id) FROM request_logs)"
        )
        record_request_log(conn, "exa", "/search", relay_key_id, 200, 5, 10, 20, None, None)

        removed = prune_request_logs(conn, retention_days=30)
        remaining = list_request_logs(conn, limit=10)

    assert removed == 1
    assert len(remaining) == 1


def test_count_group_provider_requests_today_uses_start_of_day_rewrite(tmp_path):
    # The rewritten predicate must still count today's rows (UTC) and ignore
    # yesterday's, matching the old date(created_at) = date('now') semantics.
    db_path = tmp_path / "test.sqlite3"
    with connect(str(db_path)) as conn:
        init_db(conn)
        group = create_group(conn, "pool-a", platform="exa")
        relay_key_id = create_relay_key(conn, "test", "hash", None)
        record_request_log(
            conn, "exa", "/search", relay_key_id, 200, 5, 10, 20, None, None,
            provider_group_id=group, provider_group_name="pool-a",
        )
        conn.execute(
            "UPDATE request_logs SET created_at = datetime('now', '-2 days') "
            "WHERE id = (SELECT MAX(id) FROM request_logs)"
        )

        today_count = count_group_provider_requests_today(conn, "exa", group)
        key_today = count_key_requests_today(conn, relay_key_id)

    assert today_count == 0
    assert key_today == 0
