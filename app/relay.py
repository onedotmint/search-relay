import json
import time
from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.providers import ProviderRouteError, build_upstream_request
from app.repositories import (
    count_group_provider_requests_today,
    count_key_requests_today,
    build_search_cache_key,
    enforce_search_cache_max_rows,
    get_candidate_provider_api_keys,
    get_cache_settings,
    get_search_cache,
    get_provider,
    get_relay_key_provider_groups,
    mark_provider_api_key_error,
    mark_provider_api_key_exhausted,
    mark_provider_api_key_invalid,
    mark_provider_api_key_used,
    record_request_log,
    store_search_cache,
)
from app.security import verify_secret


router = APIRouter()


def relay_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def parse_bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not header.lower().startswith(prefix.lower()):
        return None
    return header[len(prefix) :].strip()


def sanitize_json_body_for_auth(body: bytes) -> tuple[str | None, bytes]:
    if not body.strip().startswith(b"{"):
        return None, body
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, body
    if not isinstance(payload, dict):
        return None, body

    raw_key = None
    changed = False
    for key_name in ("api_key", "apiKey"):
        value = payload.pop(key_name, None)
        if isinstance(value, str) and raw_key is None:
            raw_key = value.strip()
        if value is not None:
            changed = True

    if not changed:
        return None, body
    sanitized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return raw_key, sanitized


def sanitize_query_for_auth(raw_query: str) -> tuple[str | None, str, bool]:
    if not raw_query:
        return None, "", False
    raw_key = None
    no_cache = False
    sanitized_pairs = []
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        if key in {"api_key", "apiKey"}:
            if raw_key is None:
                raw_key = value.strip()
            continue
        if key == "no_cache":
            no_cache = value.lower() == "true"
            continue
        sanitized_pairs.append((key, value))
    return raw_key, urlencode(sanitized_pairs), no_cache


def extract_relay_auth(request: Request, body: bytes) -> tuple[str | None, bytes, str, bool]:
    body_key, sanitized_body = sanitize_json_body_for_auth(body)
    query_key, sanitized_query, no_cache = sanitize_query_for_auth(request.url.query)
    return parse_bearer_token(request) or body_key or query_key, sanitized_body, sanitized_query, no_cache


def find_relay_key(conn, raw_key: str):
    rows = conn.execute(
        """
        SELECT
            relay_keys.*,
            exa_groups.name AS exa_group_name,
            exa_groups.enabled AS exa_group_enabled,
            tavily_groups.name AS tavily_group_name,
            tavily_groups.enabled AS tavily_group_enabled
        FROM relay_keys
        LEFT JOIN groups AS exa_groups ON exa_groups.id = relay_keys.exa_group_id
        LEFT JOIN groups AS tavily_groups ON tavily_groups.id = relay_keys.tavily_group_id
        WHERE relay_keys.enabled IN (0, 1)
        """
    ).fetchall()
    for row in rows:
        if verify_secret(raw_key, row["key_hash"]):
            return dict(row)
    return None


def group_sort_key(conn, provider: str, group: dict, candidates: list[dict]) -> tuple[int, int, int]:
    request_count = count_group_provider_requests_today(conn, provider, int(group["id"]))
    remaining_quota = sum(int(key["total_quota"]) - int(key["used_quota"]) for key in candidates)
    return (request_count, -remaining_quota, int(group["id"]))


def build_group_candidates(conn, relay_key_id: int, provider: str) -> tuple[list[dict], list[dict]]:
    groups = get_relay_key_provider_groups(conn, relay_key_id, provider)
    enabled_groups = [group for group in groups if int(group["enabled"]) == 1]
    group_candidates = []
    for group in enabled_groups:
        candidates = get_candidate_provider_api_keys(conn, provider, int(group["id"]))
        if candidates:
            group_candidates.append({"group": group, "keys": candidates})
    group_candidates.sort(key=lambda item: group_sort_key(conn, provider, item["group"], item["keys"]))
    return groups, group_candidates


def http_client_kwargs(request: Request, group: dict) -> dict:
    kwargs = {"timeout": request.app.state.settings.upstream_timeout_seconds}
    proxy = group.get("socks5_proxy")
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


async def forward_request(request: Request, provider: str, endpoint: str) -> Response:
    started = time.perf_counter()
    conn = request.app.state.db
    body = await request.body()

    raw_key, body, raw_query, no_cache = extract_relay_auth(request, body)
    if not raw_key:
        return relay_error(401, "relay_auth_failed", "Missing relay key")

    relay_key = find_relay_key(conn, raw_key)
    if relay_key is None:
        return relay_error(401, "relay_auth_failed", "Invalid relay key")

    relay_key_id = int(relay_key["id"])
    if int(relay_key["enabled"]) != 1:
        return relay_error(403, "relay_key_disabled", "Relay key is disabled")

    daily_limit = relay_key["daily_limit"]
    if daily_limit is not None and count_key_requests_today(conn, relay_key_id) >= int(daily_limit):
        return relay_error(429, "daily_limit_exceeded", "Relay key daily limit exceeded")

    provider_groups = get_relay_key_provider_groups(conn, relay_key_id, provider)
    if not provider_groups:
        return relay_error(403, "provider_group_unassigned", "Relay key is not assigned to this provider")
    if not any(int(group["enabled"]) == 1 for group in provider_groups):
        return relay_error(403, "group_disabled", "Relay key group is disabled")

    provider_row = get_provider(conn, provider)
    if provider_row is None or int(provider_row["enabled"]) != 1:
        record_request_log(
            conn,
            provider,
            f"/{endpoint}",
            relay_key_id,
            503,
            int((time.perf_counter() - started) * 1000),
            len(body),
            0,
            "provider_unavailable",
            "Provider is disabled or missing an API key",
        )
        return relay_error(503, "provider_unavailable", "Provider is disabled or missing an API key")

    try:
        build_upstream_request(provider, endpoint, "", raw_query)
    except ProviderRouteError:
        return relay_error(404, "unsupported_route", "Unsupported provider route")

    _, group_candidates = build_group_candidates(conn, relay_key_id, provider)
    if not group_candidates:
        record_request_log(
            conn,
            provider,
            f"/{endpoint}",
            relay_key_id,
            503,
            int((time.perf_counter() - started) * 1000),
            len(body),
            0,
            "provider_unavailable",
            "Provider is disabled or missing an API key",
        )
        return relay_error(503, "provider_unavailable", "Provider is disabled or missing an API key")

    cache_settings = get_cache_settings(
        conn,
        default_enabled=request.app.state.settings.search_cache_enabled,
        default_ttl_seconds=request.app.state.settings.search_cache_ttl_seconds,
        default_max_rows=request.app.state.settings.search_cache_max_rows,
    )
    last_retry_response: httpx.Response | None = None
    last_retry_group: dict | None = None
    last_timeout_group: dict | None = None
    last_timeout = False

    for group_candidate in group_candidates:
        group = group_candidate["group"]
        cache_key = None
        if endpoint == "search" and cache_settings["enabled"] and not no_cache:
            cache_key = build_search_cache_key(provider, endpoint, int(group["id"]), raw_query, body)
            cached = get_search_cache(conn, cache_key)
            if cached is not None:
                response_body = str(cached["response_body"]).encode("utf-8")
                record_request_log(
                    conn,
                    provider,
                    f"/{endpoint}",
                    relay_key_id,
                    int(cached["status_code"]),
                    int((time.perf_counter() - started) * 1000),
                    len(body),
                    len(response_body),
                    None,
                    None,
                    provider_group_id=int(group["id"]),
                    provider_group_name=str(group["name"]),
                )
                return Response(
                    content=response_body,
                    status_code=int(cached["status_code"]),
                    media_type=cached["content_type"],
                    headers={"X-Search-Relay-Cache": "hit"},
                )

        async with httpx.AsyncClient(**http_client_kwargs(request, group)) as client:
            for provider_key in group_candidate["keys"]:
                provider_key_id = int(provider_key["id"])
                upstream = build_upstream_request(provider, endpoint, provider_key["api_key"], raw_query)
                try:
                    upstream_response = await client.post(upstream.url, content=body, headers=upstream.headers)
                except httpx.TimeoutException:
                    last_timeout = True
                    last_timeout_group = group
                    mark_provider_api_key_error(conn, provider_key_id, 504, "Upstream request timed out")
                    continue

                if upstream_response.status_code == 401:
                    mark_provider_api_key_invalid(conn, provider_key_id, 401, upstream_response.text)
                    continue
                if upstream_response.status_code in {432, 433}:
                    mark_provider_api_key_exhausted(conn, provider_key_id, upstream_response.status_code, upstream_response.text)
                    continue
                if upstream_response.status_code in {429, 500, 502, 503, 504}:
                    mark_provider_api_key_error(conn, provider_key_id, upstream_response.status_code, upstream_response.text)
                    last_retry_response = upstream_response
                    last_retry_group = group
                    continue

                if upstream_response.status_code < 400:
                    mark_provider_api_key_used(conn, provider_key_id)
                response_body = upstream_response.content
                content_type = upstream_response.headers.get("content-type", "application/json")
                if cache_key and 200 <= upstream_response.status_code < 300:
                    store_search_cache(
                        conn,
                        cache_key,
                        provider,
                        endpoint,
                        body,
                        response_body,
                        upstream_response.status_code,
                        content_type,
                        ttl_seconds=int(cache_settings["ttl_seconds"]),
                    )
                    enforce_search_cache_max_rows(conn, int(cache_settings["max_rows"]))
                record_request_log(
                    conn,
                    provider,
                    f"/{endpoint}",
                    relay_key_id,
                    upstream_response.status_code,
                    int((time.perf_counter() - started) * 1000),
                    len(body),
                    len(response_body),
                    None if upstream_response.status_code < 400 else "upstream_error",
                    None if upstream_response.status_code < 400 else response_body[:500].decode("utf-8", "replace"),
                    provider_group_id=int(group["id"]),
                    provider_group_name=str(group["name"]),
                )
                return Response(
                    content=response_body,
                    status_code=upstream_response.status_code,
                    media_type=content_type,
                    headers={"X-Search-Relay-Cache": "miss"} if cache_key else None,
                )

    if last_retry_response is not None:
        response_body = last_retry_response.content
        record_request_log(
            conn,
            provider,
            f"/{endpoint}",
            relay_key_id,
            last_retry_response.status_code,
            int((time.perf_counter() - started) * 1000),
            len(body),
            len(response_body),
            "upstream_error",
            response_body[:500].decode("utf-8", "replace"),
            provider_group_id=int(last_retry_group["id"]) if last_retry_group else None,
            provider_group_name=str(last_retry_group["name"]) if last_retry_group else None,
        )
        return Response(
            content=response_body,
            status_code=last_retry_response.status_code,
            media_type=last_retry_response.headers.get("content-type", "application/json"),
        )

    status_code = 504 if last_timeout else 503
    error_code = "upstream_timeout" if last_timeout else "provider_unavailable"
    error_message = "Upstream request timed out" if last_timeout else "Provider is disabled or missing an API key"
    record_request_log(
        conn,
        provider,
        f"/{endpoint}",
        relay_key_id,
        status_code,
        int((time.perf_counter() - started) * 1000),
        len(body),
        0,
        error_code,
        error_message,
        provider_group_id=int(last_timeout_group["id"]) if last_timeout_group else None,
        provider_group_name=str(last_timeout_group["name"]) if last_timeout_group else None,
    )
    return relay_error(status_code, error_code, error_message)


@router.post("/exa/{endpoint}")
async def exa_relay(endpoint: str, request: Request) -> Response:
    return await forward_request(request, "exa", endpoint)


@router.post("/tavily/{endpoint}")
async def tavily_relay(endpoint: str, request: Request) -> Response:
    return await forward_request(request, "tavily", endpoint)
