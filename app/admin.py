import httpx
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.repositories import (
    clear_search_cache,
    count_request_logs,
    count_search_cache_entries,
    create_group,
    create_provider_api_key,
    create_provider,
    create_relay_key,
    dashboard_metrics,
    delete_search_cache,
    delete_relay_key,
    delete_provider_api_key,
    get_group,
    get_cache_settings,
    get_provider,
    get_request_log,
    get_relay_key,
    get_setting,
    key_preview,
    list_groups,
    list_provider_api_keys,
    list_providers,
    list_request_logs,
    list_relay_keys,
    list_search_cache_entries,
    prune_expired_search_cache,
    recent_request_logs,
    search_cache_stats,
    set_group_enabled,
    set_cache_settings,
    set_provider_api_key_enabled,
    set_provider_api_key_usage,
    set_relay_key_enabled,
    set_setting,
    update_group,
    update_provider_api_key,
    update_relay_key,
)
from app.security import (
    create_session_token,
    generate_relay_key,
    hash_secret,
    verify_secret,
    verify_session_token,
)


router = APIRouter(prefix="/admin", include_in_schema=False)
api_router = APIRouter(prefix="/api/admin", include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")
SESSION_COOKIE = "admin_session"
VALID_PROVIDERS = {"exa", "tavily"}


class LoginPayload(BaseModel):
    password: str


class ProviderPayload(BaseModel):
    api_key: str | None = None
    enabled: bool = False


class ProviderKeyPayload(BaseModel):
    label: str
    api_key: str
    group_id: int | None = None
    total_quota: int = Field(default=1000, ge=1)
    enabled: bool = True


class ProviderKeyUpdatePayload(BaseModel):
    label: str
    api_key: str | None = None
    group_id: int | None = None
    total_quota: int = Field(default=1000, ge=1)
    enabled: bool = True


class GroupPayload(BaseModel):
    name: str
    platform: str = "exa"
    enabled: bool = True
    socks5_proxy: str | None = None


class GroupUpdatePayload(BaseModel):
    name: str
    enabled: bool = True
    socks5_proxy: str | None = None


class RelayKeyPayload(BaseModel):
    label: str
    exa_group_id: int | None = None
    tavily_group_id: int | None = None
    exa_group_ids: list[int] | None = None
    tavily_group_ids: list[int] | None = None
    daily_limit: int | None = Field(default=None, ge=0)


class RelayKeyUpdatePayload(BaseModel):
    label: str
    exa_group_id: int | None = None
    tavily_group_id: int | None = None
    exa_group_ids: list[int] | None = None
    tavily_group_ids: list[int] | None = None
    daily_limit: int | None = Field(default=None, ge=0)
    enabled: bool = True


class CacheSettingsPayload(BaseModel):
    enabled: bool = True
    ttl_seconds: int = Field(default=43200, ge=1)
    max_rows: int = Field(default=10000, ge=1)


class PasswordPayload(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)


def current_admin(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        payload = verify_session_token(token, request.app.state.settings.secret_key)
        return bool(payload.get("admin"))
    except ValueError:
        return False


def require_admin(request: Request):
    if not current_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


def admin_api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def require_api_admin(request: Request) -> JSONResponse | None:
    if current_admin(request):
        return None
    return admin_api_error(401, "admin_auth_required", "Admin authentication required")


def provider_key_public(provider_key: dict) -> dict:
    total_quota = int(provider_key["total_quota"])
    used_quota = int(provider_key["used_quota"])
    return {
        "id": provider_key["id"],
        "provider_name": provider_key["provider_name"],
        "group_id": provider_key["group_id"],
        "group_name": provider_key.get("group_name"),
        "label": provider_key["label"],
        "enabled": bool(provider_key["enabled"]),
        "total_quota": total_quota,
        "used_quota": used_quota,
        "remaining_quota": max(total_quota - used_quota, 0),
        "is_invalid": bool(provider_key["is_invalid"]),
        "last_error": provider_key["last_error"],
        "last_status_code": provider_key["last_status_code"],
        "last_synced_at": provider_key["last_synced_at"],
        "key_preview": key_preview(provider_key["api_key"]),
        "use_count": provider_key["use_count"],
        "last_used_at": provider_key["last_used_at"],
        "created_at": provider_key["created_at"],
        "updated_at": provider_key["updated_at"],
    }


def normalize_proxy(value: str | None) -> str | None:
    if value is None:
        return None
    proxy = value.strip()
    return proxy or None


def validate_proxy_or_error(value: str | None) -> tuple[str | None, JSONResponse | None]:
    proxy = normalize_proxy(value)
    if proxy is None:
        return None, None
    if proxy.startswith(("socks5://", "socks5h://")):
        return proxy, None
    return None, admin_api_error(400, "invalid_group_proxy", "Group proxy must start with socks5:// or socks5h://")


def group_ids_from_payload(group_ids: list[int] | None, legacy_group_id: int | None) -> list[int]:
    values = group_ids if group_ids is not None else ([] if legacy_group_id is None else [legacy_group_id])
    normalized: list[int] = []
    for group_id in values:
        value = int(group_id)
        if value not in normalized:
            normalized.append(value)
    return normalized


def group_public(group: dict) -> dict:
    return {
        "id": group["id"],
        "name": group["name"],
        "platform": group["platform"],
        "enabled": bool(group["enabled"]),
        "socks5_proxy": group.get("socks5_proxy"),
        "created_at": group["created_at"],
        "updated_at": group["updated_at"],
    }


def relay_group_public(group: dict) -> dict:
    return {
        "id": group["id"],
        "name": group["name"],
        "platform": group["platform"],
        "enabled": bool(group["enabled"]),
        "socks5_proxy": group.get("socks5_proxy"),
    }


def provider_public(provider: dict, conn) -> dict:
    upstream_keys = [provider_key_public(key) for key in list_provider_api_keys(conn, provider["name"])]
    return {
        "name": provider["name"],
        "base_url": provider["base_url"],
        "enabled": bool(provider["enabled"]),
        "has_api_key": bool(upstream_keys),
        "upstream_key_count": len(upstream_keys),
        "upstream_keys": upstream_keys,
        "created_at": provider["created_at"],
        "updated_at": provider["updated_at"],
    }


def relay_key_public(relay_key: dict) -> dict:
    raw_key = relay_key.get("key_value") or ""
    exa_groups = [relay_group_public(group) for group in relay_key.get("exa_groups", [])]
    tavily_groups = [relay_group_public(group) for group in relay_key.get("tavily_groups", [])]
    exa_first = exa_groups[0] if exa_groups else None
    tavily_first = tavily_groups[0] if tavily_groups else None
    return {
        "id": relay_key["id"],
        "label": relay_key["label"],
        "group_id": exa_first["id"] if exa_first else relay_key["group_id"],
        "group_name": exa_first["name"] if exa_first else relay_key.get("group_name"),
        "exa_group_id": exa_first["id"] if exa_first else None,
        "exa_group_name": exa_first["name"] if exa_first else None,
        "exa_groups": exa_groups,
        "tavily_group_id": tavily_first["id"] if tavily_first else None,
        "tavily_group_name": tavily_first["name"] if tavily_first else None,
        "tavily_groups": tavily_groups,
        "enabled": bool(relay_key["enabled"]),
        "daily_limit": relay_key["daily_limit"],
        "key_preview": key_preview(raw_key),
        "has_key_value": bool(raw_key),
        "created_at": relay_key["created_at"],
    }


def cache_settings_public(request: Request) -> dict:
    defaults = request.app.state.settings
    return get_cache_settings(
        request.app.state.db,
        default_enabled=defaults.search_cache_enabled,
        default_ttl_seconds=defaults.search_cache_ttl_seconds,
        default_max_rows=defaults.search_cache_max_rows,
    )


def get_matching_group_or_error(request: Request, group_id: int | None, provider_name: str) -> tuple[dict | None, JSONResponse | None]:
    if group_id is None:
        return None, None
    group = get_group(request.app.state.db, group_id)
    if group is None:
        return None, admin_api_error(400, "group_not_found", "Group not found")
    if group["platform"] != provider_name:
        return None, admin_api_error(400, "group_platform_mismatch", "Group platform does not match provider")
    return group, None


def validate_matching_groups_or_error(request: Request, group_ids: list[int], provider_name: str) -> JSONResponse | None:
    for group_id in group_ids:
        _, group_error = get_matching_group_or_error(request, group_id, provider_name)
        if group_error:
            return group_error
    return None


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form(...)):
    admin_hash = get_setting(request.app.state.db, "admin_password_hash")
    if not admin_hash or not verify_secret(password, admin_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid password"},
            status_code=401,
        )

    response = RedirectResponse("/admin/dashboard", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token({"admin": True}, request.app.state.settings.secret_key),
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    redirect = require_admin(request)
    if redirect:
        return redirect
    metrics = dashboard_metrics(request.app.state.db)
    logs = recent_request_logs(request.app.state.db, limit=10)
    return templates.TemplateResponse(request, "dashboard.html", {"metrics": metrics, "logs": logs})


@router.get("/providers", response_class=HTMLResponse)
def providers_page(request: Request):
    redirect = require_admin(request)
    if redirect:
        return redirect
    providers = list_providers(request.app.state.db)
    return templates.TemplateResponse(request, "providers.html", {"providers": providers})


@router.post("/providers/{provider_name}")
def update_provider(
    request: Request,
    provider_name: str,
    api_key: str = Form(""),
    enabled: str | None = Form(None),
):
    redirect = require_admin(request)
    if redirect:
        return redirect
    if provider_name not in {"exa", "tavily"}:
        return RedirectResponse("/admin/providers", status_code=303)
    create_provider(request.app.state.db, provider_name, api_key.strip(), enabled == "on")
    return RedirectResponse("/admin/providers", status_code=303)


@router.get("/relay-keys", response_class=HTMLResponse)
def relay_keys_page(request: Request, created: str | None = None):
    redirect = require_admin(request)
    if redirect:
        return redirect
    keys = list_relay_keys(request.app.state.db)
    return templates.TemplateResponse(request, "relay_keys.html", {"keys": keys, "created": created})


@router.post("/relay-keys")
def create_relay_key_route(
    request: Request,
    label: str = Form(...),
    daily_limit: str = Form(""),
):
    redirect = require_admin(request)
    if redirect:
        return redirect
    raw_key = generate_relay_key()
    parsed_limit = int(daily_limit) if daily_limit.strip() else None
    create_relay_key(request.app.state.db, label.strip(), hash_secret(raw_key), parsed_limit, key_value=raw_key)
    return RedirectResponse(f"/admin/relay-keys?created={raw_key}", status_code=303)


@router.post("/relay-keys/{key_id}/enable")
def enable_relay_key(request: Request, key_id: int):
    redirect = require_admin(request)
    if redirect:
        return redirect
    set_relay_key_enabled(request.app.state.db, key_id, True)
    return RedirectResponse("/admin/relay-keys", status_code=303)


@router.post("/relay-keys/{key_id}/disable")
def disable_relay_key(request: Request, key_id: int):
    redirect = require_admin(request)
    if redirect:
        return redirect
    set_relay_key_enabled(request.app.state.db, key_id, False)
    return RedirectResponse("/admin/relay-keys", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    redirect = require_admin(request)
    if redirect:
        return redirect
    logs = recent_request_logs(request.app.state.db, limit=100)
    return templates.TemplateResponse(request, "logs.html", {"logs": logs})


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    redirect = require_admin(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "settings.html")


@router.post("/settings", response_class=HTMLResponse)
def update_settings(request: Request, password: str = Form(...)):
    redirect = require_admin(request)
    if redirect:
        return redirect
    set_setting(request.app.state.db, "admin_password_hash", hash_secret(password))
    return RedirectResponse("/admin/settings", status_code=303)


@api_router.post("/login")
def api_login(request: Request, payload: LoginPayload):
    admin_hash = get_setting(request.app.state.db, "admin_password_hash")
    if not admin_hash or not verify_secret(payload.password, admin_hash):
        return admin_api_error(401, "invalid_admin_password", "Invalid password")

    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token({"admin": True}, request.app.state.settings.secret_key),
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


@api_router.post("/logout")
def api_logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE)
    return response


@api_router.get("/me")
def api_me(request: Request):
    return {"authenticated": current_admin(request)}


@api_router.get("/dashboard")
def api_dashboard(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    metrics = dashboard_metrics(request.app.state.db)
    logs = recent_request_logs(request.app.state.db, limit=10)
    return {"metrics": metrics, "recent_logs": logs}


@api_router.get("/providers")
def api_providers(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    providers = [provider_public(provider, request.app.state.db) for provider in list_providers(request.app.state.db)]
    return {"providers": providers}


@api_router.get("/groups")
def api_groups(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    groups = [group_public(group) for group in list_groups(request.app.state.db)]
    return {"groups": groups}


@api_router.post("/groups")
def api_create_group(request: Request, payload: GroupPayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if not payload.name.strip():
        return admin_api_error(400, "invalid_group", "Group name is required")
    if payload.platform not in VALID_PROVIDERS:
        return admin_api_error(400, "invalid_group", "Group platform is not supported")
    socks5_proxy, proxy_error = validate_proxy_or_error(payload.socks5_proxy)
    if proxy_error:
        return proxy_error
    try:
        group_id = create_group(
            request.app.state.db,
            payload.name.strip(),
            payload.platform,
            payload.enabled,
            socks5_proxy=socks5_proxy,
        )
    except Exception:
        return admin_api_error(400, "invalid_group", "Group name already exists")
    group = next(group for group in list_groups(request.app.state.db) if group["id"] == group_id)
    return {"group": group_public(group)}


@api_router.put("/groups/{group_id}")
def api_update_group(request: Request, group_id: int, payload: GroupUpdatePayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    existing = get_group(request.app.state.db, group_id)
    if existing is None:
        return admin_api_error(404, "group_not_found", "Group not found")
    if not payload.name.strip():
        return admin_api_error(400, "invalid_group", "Group name is required")
    socks5_proxy, proxy_error = validate_proxy_or_error(payload.socks5_proxy)
    if proxy_error:
        return proxy_error
    try:
        update_group(
            request.app.state.db,
            group_id,
            payload.name.strip(),
            payload.enabled,
            socks5_proxy=socks5_proxy,
        )
    except Exception:
        return admin_api_error(400, "invalid_group", "Group name already exists")
    group = next(group for group in list_groups(request.app.state.db) if group["id"] == group_id)
    return {"group": group_public(group)}


@api_router.post("/groups/{group_id}/enable")
def api_enable_group(request: Request, group_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    set_group_enabled(request.app.state.db, group_id, True)
    return {"ok": True}


@api_router.post("/groups/{group_id}/disable")
def api_disable_group(request: Request, group_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    set_group_enabled(request.app.state.db, group_id, False)
    return {"ok": True}


@api_router.put("/providers/{provider_name}")
def api_update_provider(request: Request, provider_name: str, payload: ProviderPayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name not in VALID_PROVIDERS:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    existing = next((provider for provider in list_providers(request.app.state.db) if provider["name"] == provider_name), None)
    api_key = payload.api_key.strip() if payload.api_key is not None else ""
    if not api_key and existing:
        api_key = existing["api_key"] or ""
    create_provider(request.app.state.db, provider_name, api_key, payload.enabled)
    provider = next(provider for provider in list_providers(request.app.state.db) if provider["name"] == provider_name)
    return {"provider": provider_public(provider, request.app.state.db)}


@api_router.post("/providers/{provider_name}/keys")
def api_create_provider_key(request: Request, provider_name: str, payload: ProviderKeyPayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name not in VALID_PROVIDERS:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    if not payload.label.strip() or not payload.api_key.strip():
        return admin_api_error(400, "invalid_provider_key", "Provider key label and API key are required")
    _, group_error = get_matching_group_or_error(request, payload.group_id, provider_name)
    if group_error:
        return group_error
    key_id = create_provider_api_key(
        request.app.state.db,
        provider_name,
        payload.label.strip(),
        payload.api_key.strip(),
        payload.enabled,
        payload.group_id,
        payload.total_quota,
    )
    key = next(key for key in list_provider_api_keys(request.app.state.db, provider_name) if key["id"] == key_id)
    return {"upstream_key": provider_key_public(key)}


@api_router.put("/providers/{provider_name}/keys/{key_id}")
def api_update_provider_key(request: Request, provider_name: str, key_id: int, payload: ProviderKeyUpdatePayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name not in VALID_PROVIDERS:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    existing = next(
        (key for key in list_provider_api_keys(request.app.state.db, provider_name) if key["id"] == key_id),
        None,
    )
    if existing is None:
        return admin_api_error(404, "provider_key_not_found", "Provider key not found")
    if not payload.label.strip():
        return admin_api_error(400, "invalid_provider_key", "Provider key label is required")
    _, group_error = get_matching_group_or_error(request, payload.group_id, provider_name)
    if group_error:
        return group_error
    update_provider_api_key(
        request.app.state.db,
        provider_name,
        key_id,
        payload.label.strip(),
        payload.api_key,
        payload.enabled,
        payload.group_id,
        payload.total_quota,
    )
    key = next(key for key in list_provider_api_keys(request.app.state.db, provider_name) if key["id"] == key_id)
    return {"upstream_key": provider_key_public(key)}


@api_router.post("/providers/{provider_name}/keys/{key_id}/sync-usage")
async def api_sync_provider_key_usage(request: Request, provider_name: str, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name != "tavily":
        return admin_api_error(400, "usage_sync_unsupported", "Usage sync is only supported for Tavily")
    provider = get_provider(request.app.state.db, provider_name)
    if provider is None:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    key = next((key for key in list_provider_api_keys(request.app.state.db, provider_name) if key["id"] == key_id), None)
    if key is None:
        return admin_api_error(404, "provider_key_not_found", "Provider key not found")

    try:
        async with httpx.AsyncClient(timeout=request.app.state.settings.upstream_timeout_seconds) as client:
            response = await client.get(
                provider["base_url"].rstrip("/") + "/usage",
                headers={"Authorization": f"Bearer {key['api_key']}"},
            )
    except httpx.TimeoutException:
        return admin_api_error(504, "usage_sync_timeout", "Usage sync timed out")
    if response.status_code != 200:
        return admin_api_error(502, "usage_sync_failed", response.text[:500] or "Usage sync failed")

    payload = response.json()
    usage = int(payload.get("key", {}).get("usage", 0))
    limit = payload.get("key", {}).get("limit")
    if limit is None:
        limit = payload.get("account", {}).get("plan_limit")
    set_provider_api_key_usage(
        request.app.state.db,
        key_id,
        used_quota=usage,
        total_quota=int(limit) if limit is not None else None,
        synced=True,
    )
    synced = next(key for key in list_provider_api_keys(request.app.state.db, provider_name) if key["id"] == key_id)
    return {"upstream_key": provider_key_public(synced)}


@api_router.post("/providers/{provider_name}/keys/{key_id}/enable")
def api_enable_provider_key(request: Request, provider_name: str, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name not in VALID_PROVIDERS:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    set_provider_api_key_enabled(request.app.state.db, provider_name, key_id, True)
    return {"ok": True}


@api_router.post("/providers/{provider_name}/keys/{key_id}/disable")
def api_disable_provider_key(request: Request, provider_name: str, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name not in VALID_PROVIDERS:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    set_provider_api_key_enabled(request.app.state.db, provider_name, key_id, False)
    return {"ok": True}


@api_router.delete("/providers/{provider_name}/keys/{key_id}")
def api_delete_provider_key(request: Request, provider_name: str, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider_name not in VALID_PROVIDERS:
        return admin_api_error(404, "provider_not_found", "Provider not found")
    delete_provider_api_key(request.app.state.db, provider_name, key_id)
    return {"ok": True}


@api_router.get("/relay-keys")
def api_relay_keys(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    keys = [relay_key_public(key) for key in list_relay_keys(request.app.state.db)]
    return {"relay_keys": keys}


@api_router.post("/relay-keys")
def api_create_relay_key(request: Request, payload: RelayKeyPayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    label = payload.label.strip()
    if not label:
        return admin_api_error(400, "invalid_relay_key", "Relay key label is required")
    exa_group_ids = group_ids_from_payload(payload.exa_group_ids, payload.exa_group_id)
    tavily_group_ids = group_ids_from_payload(payload.tavily_group_ids, payload.tavily_group_id)
    exa_group_error = validate_matching_groups_or_error(request, exa_group_ids, "exa")
    if exa_group_error:
        return exa_group_error
    tavily_group_error = validate_matching_groups_or_error(request, tavily_group_ids, "tavily")
    if tavily_group_error:
        return tavily_group_error
    raw_key = generate_relay_key()
    key_id = create_relay_key(
        request.app.state.db,
        label,
        hash_secret(raw_key),
        payload.daily_limit,
        key_value=raw_key,
        exa_group_ids=exa_group_ids,
        tavily_group_ids=tavily_group_ids,
        assign_default_groups=False,
    )
    created = next(key for key in list_relay_keys(request.app.state.db) if key["id"] == key_id)
    return {"relay_key": raw_key, "record": relay_key_public(created)}


@api_router.put("/relay-keys/{key_id}")
def api_update_relay_key(request: Request, key_id: int, payload: RelayKeyUpdatePayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    existing = get_relay_key(request.app.state.db, key_id)
    if existing is None:
        return admin_api_error(404, "relay_key_not_found", "Relay key not found")
    if not payload.label.strip():
        return admin_api_error(400, "invalid_relay_key", "Relay key label is required")
    exa_group_ids = group_ids_from_payload(payload.exa_group_ids, payload.exa_group_id)
    tavily_group_ids = group_ids_from_payload(payload.tavily_group_ids, payload.tavily_group_id)
    exa_group_error = validate_matching_groups_or_error(request, exa_group_ids, "exa")
    if exa_group_error:
        return exa_group_error
    tavily_group_error = validate_matching_groups_or_error(request, tavily_group_ids, "tavily")
    if tavily_group_error:
        return tavily_group_error
    update_relay_key(
        request.app.state.db,
        key_id,
        payload.label.strip(),
        payload.enabled,
        exa_group_ids[0] if exa_group_ids else None,
        tavily_group_ids[0] if tavily_group_ids else None,
        payload.daily_limit,
        exa_group_ids=exa_group_ids,
        tavily_group_ids=tavily_group_ids,
    )
    relay_key = next(key for key in list_relay_keys(request.app.state.db) if key["id"] == key_id)
    return {"relay_key": relay_key_public(relay_key)}


@api_router.get("/relay-keys/{key_id}/value")
def api_get_relay_key_value(request: Request, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    relay_key = get_relay_key(request.app.state.db, key_id)
    if relay_key is None:
        return admin_api_error(404, "relay_key_not_found", "Relay key not found")
    if not relay_key.get("key_value"):
        return admin_api_error(404, "relay_key_value_unavailable", "Relay key value is unavailable for this legacy key")
    return {"relay_key": relay_key["key_value"]}


@api_router.post("/relay-keys/{key_id}/enable")
def api_enable_relay_key(request: Request, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    set_relay_key_enabled(request.app.state.db, key_id, True)
    return {"ok": True}


@api_router.post("/relay-keys/{key_id}/disable")
def api_disable_relay_key(request: Request, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    set_relay_key_enabled(request.app.state.db, key_id, False)
    return {"ok": True}


@api_router.delete("/relay-keys/{key_id}")
def api_delete_relay_key(request: Request, key_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if get_relay_key(request.app.state.db, key_id) is None:
        return admin_api_error(404, "relay_key_not_found", "Relay key not found")
    delete_relay_key(request.app.state.db, key_id)
    return {"ok": True}


@api_router.get("/cache/settings")
def api_cache_settings(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    return {"settings": cache_settings_public(request)}


@api_router.put("/cache/settings")
def api_update_cache_settings(request: Request, payload: CacheSettingsPayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    set_cache_settings(request.app.state.db, payload.enabled, payload.ttl_seconds, payload.max_rows)
    return {"settings": cache_settings_public(request)}


@api_router.get("/cache/stats")
def api_cache_stats(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    return {"settings": cache_settings_public(request), "stats": search_cache_stats(request.app.state.db)}


@api_router.get("/cache")
def api_cache_entries(
    request: Request,
    provider: str | None = None,
    status: str = "all",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider is not None and provider not in VALID_PROVIDERS:
        return admin_api_error(400, "invalid_cache_filter", "Provider is not supported")
    if status not in {"all", "active", "expired"}:
        return admin_api_error(400, "invalid_cache_filter", "Cache status is not supported")
    entries = list_search_cache_entries(request.app.state.db, provider=provider, status=status, limit=limit, offset=offset)
    total = count_search_cache_entries(request.app.state.db, provider=provider, status=status)
    return {"entries": entries, "total": total, "limit": limit, "offset": offset}


@api_router.delete("/cache/{cache_id}")
def api_delete_cache_entry(request: Request, cache_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if not delete_search_cache(request.app.state.db, cache_id):
        return admin_api_error(404, "cache_entry_not_found", "Cache entry not found")
    return {"ok": True}


@api_router.post("/cache/prune")
def api_prune_cache(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    return {"deleted": prune_expired_search_cache(request.app.state.db)}


@api_router.post("/cache/clear")
def api_clear_cache(request: Request):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    return {"deleted": clear_search_cache(request.app.state.db)}


@api_router.get("/logs")
def api_logs(
    request: Request,
    provider: str | None = None,
    status: str = "all",
    relay_key_id: int | None = None,
    endpoint: str | None = None,
    q: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    if provider is not None and provider not in VALID_PROVIDERS:
        return admin_api_error(400, "invalid_log_filter", "Provider is not supported")
    if status not in {"all", "success", "error", "client_error", "server_error"}:
        return admin_api_error(400, "invalid_log_filter", "Log status is not supported")
    logs = list_request_logs(
        request.app.state.db,
        provider=provider,
        status=status,
        relay_key_id=relay_key_id,
        endpoint=endpoint,
        q=q,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    total = count_request_logs(
        request.app.state.db,
        provider=provider,
        status=status,
        relay_key_id=relay_key_id,
        endpoint=endpoint,
        q=q,
        created_from=created_from,
        created_to=created_to,
    )
    return {"logs": logs, "total": total, "limit": limit, "offset": offset}


@api_router.get("/logs/{log_id}")
def api_log_detail(request: Request, log_id: int):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    log = get_request_log(request.app.state.db, log_id)
    if log is None:
        return admin_api_error(404, "log_not_found", "Log not found")
    return {"log": log}


@api_router.post("/settings/password")
def api_update_password(request: Request, payload: PasswordPayload):
    auth_error = require_api_admin(request)
    if auth_error:
        return auth_error
    admin_hash = get_setting(request.app.state.db, "admin_password_hash")
    if not admin_hash or not verify_secret(payload.current_password, admin_hash):
        return admin_api_error(401, "invalid_current_password", "Current password is invalid")
    set_setting(request.app.state.db, "admin_password_hash", hash_secret(payload.new_password))
    return {"ok": True}
