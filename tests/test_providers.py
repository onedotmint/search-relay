import pytest

from app.providers import (
    PROVIDERS,
    AuthStrategy,
    ProviderConfig,
    ProviderRouteError,
    RouteConfig,
    build_upstream_request,
    provider_base_url,
    resolve_route,
)


def test_exa_search_uses_x_api_key_header():
    request = build_upstream_request("exa", "search", api_key="exa-secret")

    assert request.url == "https://api.exa.ai/search"
    assert request.headers["x-api-key"] == "exa-secret"
    assert request.headers["Content-Type"] == "application/json"


def test_tavily_search_uses_bearer_header():
    request = build_upstream_request("tavily", "search", api_key="tvly-secret")

    assert request.url == "https://api.tavily.com/search"
    assert request.headers["Authorization"] == "Bearer tvly-secret"
    assert request.headers["Content-Type"] == "application/json"


def test_unknown_provider_route_is_rejected():
    with pytest.raises(ProviderRouteError):
        build_upstream_request("exa", "crawl", api_key="exa-secret")


def test_exa_route_resolution_from_registry():
    route = resolve_route("exa", "search")

    assert route.upstream_url == "https://api.exa.ai/search"
    assert route.methods == frozenset({"POST"})
    assert "POST" in route.methods
    assert route.auth.kind == "header"
    assert route.auth.header_name == "x-api-key"


def test_tavily_route_resolution_from_registry():
    route = resolve_route("tavily", "search")

    assert route.upstream_url == "https://api.tavily.com/search"
    assert "POST" in route.methods
    assert route.auth.kind == "bearer"


def test_unknown_provider_rejected():
    with pytest.raises(ProviderRouteError):
        resolve_route("firecrawl", "search")

    with pytest.raises(ProviderRouteError):
        build_upstream_request("firecrawl", "search", api_key="secret")


def test_unknown_route_rejected():
    with pytest.raises(ProviderRouteError):
        resolve_route("exa", "crawl")


def test_unknown_method_rejected():
    with pytest.raises(ProviderRouteError):
        resolve_route("exa", "search", method="GET")

    with pytest.raises(ProviderRouteError):
        build_upstream_request("exa", "search", api_key="exa-secret", method="GET")


def test_route_config_carries_cacheable_flag():
    assert PROVIDERS["exa"].routes["search"].cacheable is True
    assert PROVIDERS["tavily"].routes["search"].cacheable is True
    assert PROVIDERS["brave"].routes["search"].cacheable is True

    for provider in ("exa", "tavily", "brave"):
        for endpoint, route in PROVIDERS[provider].routes.items():
            if endpoint != "search":
                assert route.cacheable is False


def test_brave_route_resolution():
    route = resolve_route("brave", "search")

    assert route.upstream_url == "https://api.search.brave.com/res/v1/search"
    assert route.methods == frozenset({"GET"})
    assert "GET" in route.methods
    assert "POST" not in route.methods
    assert route.auth.kind == "header"
    assert route.auth.header_name == "X-Subscription-Token"
    assert route.cacheable is True

    with pytest.raises(ProviderRouteError):
        resolve_route("brave", "search", method="POST")
    with pytest.raises(ProviderRouteError):
        resolve_route("brave", "contents")


def test_brave_uses_subscription_token_header():
    request = build_upstream_request("brave", "search", api_key="brave-secret", method="GET")

    assert request.url == "https://api.search.brave.com/res/v1/search"
    assert request.headers["X-Subscription-Token"] == "brave-secret"
    assert "Authorization" not in request.headers
    assert "x-api-key" not in request.headers

    with pytest.raises(ProviderRouteError):
        build_upstream_request("brave", "search", api_key="brave-secret", method="POST")


def test_provider_base_url_from_registry():
    assert provider_base_url("brave") == "https://api.search.brave.com"
    assert provider_base_url("exa") == "https://api.exa.ai"
    assert provider_base_url("tavily") == "https://api.tavily.com"
    assert provider_base_url("firecrawl") == "https://api.tavily.com"  # fallback, pre-registry provider


def test_header_auth_strategy_builds_named_header(monkeypatch):
    auth = AuthStrategy(kind="header", header_name="X-Subscription-Token")
    monkeypatch.setitem(
        PROVIDERS,
        "brave",
        ProviderConfig(
            name="brave",
            role="general_web_search",
            default_auth=auth,
            routes={
                "search": RouteConfig(
                    upstream_url="https://api.search.brave.com/res/v1/search",
                    methods=frozenset({"GET"}),
                    auth=auth,
                    cacheable=True,
                )
            },
        ),
    )

    request = build_upstream_request("brave", "search", api_key="brave-secret", method="GET")

    assert request.url == "https://api.search.brave.com/res/v1/search"
    assert request.headers["X-Subscription-Token"] == "brave-secret"
    assert "Authorization" not in request.headers


def test_bearer_auth_strategy_builds_authorization(monkeypatch):
    auth = AuthStrategy(kind="bearer")
    monkeypatch.setitem(
        PROVIDERS,
        "jina",
        ProviderConfig(
            name="jina",
            role="reader_reranker",
            default_auth=auth,
            routes={
                "rerank": RouteConfig(
                    upstream_url="https://api.jina.ai/v1/rerank",
                    methods=frozenset({"POST"}),
                    auth=auth,
                )
            },
        ),
    )

    request = build_upstream_request("jina", "rerank", api_key="jina-secret")

    assert request.url == "https://api.jina.ai/v1/rerank"
    assert request.headers["Authorization"] == "Bearer jina-secret"
    assert "x-api-key" not in request.headers


def test_jina_route_resolution():
    reader = resolve_route("jina", "reader")
    assert reader.upstream_url == "https://r.jina.ai/"
    assert reader.methods == frozenset({"GET"})
    assert "GET" in reader.methods
    assert "POST" not in reader.methods
    assert reader.auth.kind == "bearer"
    assert reader.cacheable is False
    assert reader.path_style == "path"

    rerank = resolve_route("jina", "rerank")
    assert rerank.upstream_url == "https://api.jina.ai/v1/rerank"
    assert rerank.methods == frozenset({"POST"})
    assert "POST" in rerank.methods
    assert "GET" not in rerank.methods
    assert rerank.auth.kind == "bearer"
    assert rerank.cacheable is False
    assert rerank.path_style == "segment"

    with pytest.raises(ProviderRouteError):
        resolve_route("jina", "reader", method="POST")
    with pytest.raises(ProviderRouteError):
        resolve_route("jina", "rerank", method="GET")
    with pytest.raises(ProviderRouteError):
        resolve_route("jina", "search")


def test_jina_uses_bearer_auth():
    request = build_upstream_request("jina", "rerank", api_key="jina-secret")

    assert request.url == "https://api.jina.ai/v1/rerank"
    assert request.headers["Authorization"] == "Bearer jina-secret"
    assert request.headers["Content-Type"] == "application/json"
    assert "x-api-key" not in request.headers
    assert "X-Subscription-Token" not in request.headers

    with pytest.raises(ProviderRouteError):
        build_upstream_request("jina", "rerank", api_key="jina-secret", method="GET")


def test_jina_reader_path_suffix_builds_url():
    request = build_upstream_request(
        "jina",
        "reader",
        api_key="jina-secret",
        method="GET",
        path_suffix="https://example.com/article",
    )

    assert request.url == "https://r.jina.ai/https://example.com/article"
    assert request.headers["Authorization"] == "Bearer jina-secret"
    # GET requests carry no body, so no Content-Type header is set.
    assert "Content-Type" not in request.headers
