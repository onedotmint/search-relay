import pytest

from app.providers import ProviderRouteError, build_upstream_request


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
