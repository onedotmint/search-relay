from dataclasses import dataclass


class ProviderRouteError(ValueError):
    pass


@dataclass(frozen=True)
class AuthStrategy:
    """How a provider route authenticates to its upstream.

    kind is "bearer" (Authorization: Bearer <key>) or "header" (send the key
    in a named header such as x-api-key / X-Subscription-Token). For
    kind == "header", header_name is required.
    """

    kind: str
    header_name: str | None = None


@dataclass(frozen=True)
class RouteConfig:
    upstream_url: str
    methods: frozenset[str]
    auth: AuthStrategy
    cacheable: bool = False
    # "segment" = endpoint is a fixed path segment (e.g. /exa/search);
    # "path" = the remaining URL path is appended to upstream_url
    # (e.g. /jina/reader/https://... -> https://r.jina.ai/https://...).
    path_style: str = "segment"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    role: str
    default_auth: AuthStrategy
    routes: dict[str, RouteConfig]
    # Management/documentation base URL (mirrors the legacy providers.base_url
    # column). Not used for relay forwarding — routes carry full upstream URLs.
    base_url: str | None = None


@dataclass(frozen=True)
class UpstreamRequest:
    url: str
    headers: dict[str, str]


_EXA_AUTH = AuthStrategy(kind="header", header_name="x-api-key")
_TAVILY_AUTH = AuthStrategy(kind="bearer")
_BRAVE_AUTH = AuthStrategy(kind="header", header_name="X-Subscription-Token")
_JINA_AUTH = AuthStrategy(kind="bearer")

PROVIDERS: dict[str, ProviderConfig] = {
    "exa": ProviderConfig(
        name="exa",
        role="semantic_specialized_search",
        default_auth=_EXA_AUTH,
        base_url="https://api.exa.ai",
        routes={
            "search": RouteConfig(
                upstream_url="https://api.exa.ai/search",
                methods=frozenset({"POST"}),
                auth=_EXA_AUTH,
                cacheable=True,
            ),
            "contents": RouteConfig(
                upstream_url="https://api.exa.ai/contents",
                methods=frozenset({"POST"}),
                auth=_EXA_AUTH,
            ),
            "answer": RouteConfig(
                upstream_url="https://api.exa.ai/answer",
                methods=frozenset({"POST"}),
                auth=_EXA_AUTH,
            ),
        },
    ),
    "tavily": ProviderConfig(
        name="tavily",
        role="search_research_crawl",
        default_auth=_TAVILY_AUTH,
        base_url="https://api.tavily.com",
        routes={
            "search": RouteConfig(
                upstream_url="https://api.tavily.com/search",
                methods=frozenset({"POST"}),
                auth=_TAVILY_AUTH,
                cacheable=True,
            ),
            "extract": RouteConfig(
                upstream_url="https://api.tavily.com/extract",
                methods=frozenset({"POST"}),
                auth=_TAVILY_AUTH,
            ),
            "crawl": RouteConfig(
                upstream_url="https://api.tavily.com/crawl",
                methods=frozenset({"POST"}),
                auth=_TAVILY_AUTH,
            ),
            "map": RouteConfig(
                upstream_url="https://api.tavily.com/map",
                methods=frozenset({"POST"}),
                auth=_TAVILY_AUTH,
            ),
            "research": RouteConfig(
                upstream_url="https://api.tavily.com/research",
                methods=frozenset({"POST"}),
                auth=_TAVILY_AUTH,
            ),
        },
    ),
    "brave": ProviderConfig(
        name="brave",
        role="general_web_search",
        default_auth=_BRAVE_AUTH,
        base_url="https://api.search.brave.com",
        routes={
            "search": RouteConfig(
                upstream_url="https://api.search.brave.com/res/v1/search",
                methods=frozenset({"GET"}),
                auth=_BRAVE_AUTH,
                cacheable=True,
            ),
        },
    ),
    "jina": ProviderConfig(
        name="jina",
        role="reader_reranker",
        default_auth=_JINA_AUTH,
        base_url="https://r.jina.ai",
        routes={
            # Reader: GET https://r.jina.ai/<url> — the target URL arrives as a
            # path suffix after /jina/reader/. Smart Search calls
            # GET <relay>/jina/reader/https://example.com/article.
            "reader": RouteConfig(
                upstream_url="https://r.jina.ai/",
                methods=frozenset({"GET"}),
                auth=_JINA_AUTH,
                path_style="path",
            ),
            # Reranker: POST https://api.jina.ai/v1/rerank with a JSON body.
            "rerank": RouteConfig(
                upstream_url="https://api.jina.ai/v1/rerank",
                methods=frozenset({"POST"}),
                auth=_JINA_AUTH,
            ),
        },
    ),
}


def resolve_route(provider: str, endpoint: str, method: str | None = None) -> RouteConfig:
    """Resolve a provider/endpoint/method triple against the registry.

    Raises ProviderRouteError for an unknown provider, an unknown endpoint,
    or a method the route does not allow.
    """
    provider_config = PROVIDERS.get(provider)
    if provider_config is None:
        raise ProviderRouteError(f"Unsupported provider: {provider}")

    route = provider_config.routes.get(endpoint)
    if route is None:
        raise ProviderRouteError(f"Unsupported provider route: {provider}/{endpoint}")

    if method is not None and method not in route.methods:
        raise ProviderRouteError(f"Unsupported method for {provider}/{endpoint}: {method}")

    return route


def provider_base_url(name: str) -> str:
    """Management base URL for a provider, from the registry when available.

    Falls back to the legacy exa/tavily hardcode for providers that predate or
    lack a registry entry (keeps create_provider() stable for callers).
    """
    config = PROVIDERS.get(name)
    if config is not None and config.base_url:
        return config.base_url
    return "https://api.exa.ai" if name == "exa" else "https://api.tavily.com"


def _auth_headers(auth: AuthStrategy, api_key: str) -> dict[str, str]:
    if auth.kind == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if auth.kind == "header":
        header_name = auth.header_name or "x-api-key"
        return {header_name: api_key}
    raise ProviderRouteError(f"Unsupported auth strategy: {auth.kind}")


def build_upstream_request(
    provider: str,
    endpoint: str,
    api_key: str,
    raw_query: str = "",
    method: str = "POST",
    path_suffix: str = "",
) -> UpstreamRequest:
    """Build the provider-native upstream request for a registry route.

    path_suffix is used by path-style routes (e.g. the Jina Reader, where the
    target URL is appended to https://r.jina.ai/). Trailing slashes on the
    upstream base are stripped so the suffix joins cleanly without a double
    slash. For segment-style routes a non-empty suffix is invalid — callers
    reject it before calling this.
    """
    route = resolve_route(provider, endpoint, method)

    headers: dict[str, str] = {}
    # GET requests carry no body, so no Content-Type is needed; the client's
    # own content-negotiation headers are forwarded separately by the relay.
    if method != "GET":
        headers["Content-Type"] = "application/json"
    headers.update(_auth_headers(route.auth, api_key))

    url = route.upstream_url
    if path_suffix:
        url = f"{route.upstream_url.rstrip('/')}/{path_suffix}"
    if raw_query:
        url += f"?{raw_query}"
    return UpstreamRequest(url=url, headers=headers)
