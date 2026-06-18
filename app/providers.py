from dataclasses import dataclass


class ProviderRouteError(ValueError):
    pass


@dataclass(frozen=True)
class UpstreamRequest:
    url: str
    headers: dict[str, str]


PROVIDER_ROUTES: dict[str, set[str]] = {
    "exa": {"search", "contents", "answer"},
    "tavily": {"search", "extract", "crawl", "map", "research"},
}

PROVIDER_BASE_URLS: dict[str, str] = {
    "exa": "https://api.exa.ai",
    "tavily": "https://api.tavily.com",
}


def build_upstream_request(provider: str, endpoint: str, api_key: str, raw_query: str = "") -> UpstreamRequest:
    if provider not in PROVIDER_ROUTES or endpoint not in PROVIDER_ROUTES[provider]:
        raise ProviderRouteError(f"Unsupported provider route: {provider}/{endpoint}")

    headers = {"Content-Type": "application/json"}
    if provider == "exa":
        headers["x-api-key"] = api_key
    elif provider == "tavily":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        raise ProviderRouteError(f"Unsupported provider: {provider}")

    url = f"{PROVIDER_BASE_URLS[provider]}/{endpoint}"
    if raw_query:
        url += f"?{raw_query}"
    return UpstreamRequest(url=url, headers=headers)
