"""Live provider smoke tests (opt-in).

These tests hit REAL third-party APIs and consume real quota. They are
collected by the default `pytest` run but each test skips unless its provider
API key is exported (CI never exports keys, so ordinary CI runs skip them
all). Enable deliberately:

    export BRAVE_API_KEY=... EXA_API_KEY=... TAVILY_API_KEY=... JINA_API_KEY=...
    pytest tests/live -v

Requests are built through `app.providers.build_upstream_request`, so a
provider contract change (route URL, auth header shape, method) surfaces
here as a failure — that is the point: detect upstream drift before it
breaks production.
"""

import os

import httpx
import pytest

from app.providers import build_upstream_request

REQUEST_TIMEOUT_SECONDS = 30


def _skip_without(env_var: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not os.getenv(env_var, "").strip(),
        reason=f"live test requires {env_var}",
    )


def _post(provider: str, endpoint: str, api_key: str, payload: dict, query: str = ""):
    request = build_upstream_request(provider, endpoint, api_key, raw_query=query, method="POST")
    response = httpx.post(request.url, json=payload, headers=request.headers, timeout=REQUEST_TIMEOUT_SECONDS)
    return request, response


def _get(provider: str, endpoint: str, api_key: str, query: str = "", path_suffix: str = ""):
    request = build_upstream_request(
        provider, endpoint, api_key, raw_query=query, method="GET", path_suffix=path_suffix
    )
    response = httpx.get(request.url, headers=request.headers, timeout=REQUEST_TIMEOUT_SECONDS)
    return request, response


@_skip_without("BRAVE_API_KEY")
def test_brave_search_live():
    _, response = _get("brave", "search", os.environ["BRAVE_API_KEY"], query="q=search+relay&count=3")

    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert "web" in body, body.keys()


@_skip_without("EXA_API_KEY")
def test_exa_search_live():
    _, response = _post(
        "exa", "search", os.environ["EXA_API_KEY"], {"query": "search relay", "numResults": 3}
    )

    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert "results" in body, body.keys()


@_skip_without("TAVILY_API_KEY")
def test_tavily_search_live():
    _, response = _post(
        "tavily", "search", os.environ["TAVILY_API_KEY"], {"query": "search relay", "max_results": 3}
    )

    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert "results" in body, body.keys()


@_skip_without("JINA_API_KEY")
def test_jina_rerank_live():
    _, response = _post(
        "jina",
        "rerank",
        os.environ["JINA_API_KEY"],
        {
            "model": "jina-reranker-v2-base-multilingual",
            "query": "search relay",
            "documents": ["a search relay gateway", "the weather today"],
            "top_n": 2,
        },
    )

    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert "results" in body, body.keys()


@_skip_without("JINA_API_KEY")
def test_jina_reader_live():
    _, response = _get(
        "jina", "reader", os.environ["JINA_API_KEY"], path_suffix="https://example.com"
    )

    assert response.status_code == 200, response.text[:500]
    # The reader returns the page content as the response body.
    assert len(response.text) > 100
