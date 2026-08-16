# API Guide

Search Relay forwards requests to provider-native APIs while adding relay authentication, upstream key selection, group routing, retry switching, logs, and optional caching.

## Authentication

Recommended:

```http
Authorization: Bearer relay_xxx
Content-Type: application/json
```

Compatibility modes:

- JSON body field: `api_key` or `apiKey`
- Query parameter: `api_key` or `apiKey`

When a key is provided in the JSON body, Search Relay removes `api_key` and `apiKey` before forwarding the request upstream.

Prefer the `Authorization` header in production because query parameters are more likely to appear in logs.

## Provider Routes

Search Relay keeps provider APIs separate. It does not define a custom aggregated `/search` request schema.

| Provider | Relay route | Upstream behavior |
| --- | --- | --- |
| Exa | `/exa/search` | Forwards to Exa search (POST) |
| Exa | `/exa/contents` | Forwards to Exa contents (POST) |
| Exa | `/exa/answer` | Forwards to Exa answer (POST) |
| Tavily | `/tavily/search` | Forwards to Tavily search (POST) |
| Tavily | `/tavily/extract` | Forwards to Tavily extract (POST) |
| Tavily | `/tavily/crawl` | Forwards to Tavily crawl (POST) |
| Tavily | `/tavily/map` | Forwards to Tavily map (POST) |
| Tavily | `/tavily/research` | Forwards to Tavily research (POST) |
| Brave | `/brave/search` | Forwards to Brave search (GET; query params forwarded) |
| Jina | `/jina/reader` | Forwards to the Jina Reader (GET; target URL as path suffix: `/jina/reader/https://example.com/article`) |
| Jina | `/jina/rerank` | Forwards to the Jina Reranker (POST; JSON body `model`/`query`/`documents`) |

Use the provider's native request body for each endpoint. GET routes forward
query parameters as-is (relay credential query parameters are stripped); POST
routes forward the raw request body after removing any relay `api_key` field.

## Exa Example

```python
import requests

response = requests.post(
    "http://127.0.0.1:8080/exa/search",
    headers={"Authorization": "Bearer relay_xxx"},
    json={
        "query": "latest AI search APIs",
        "numResults": 3,
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())
```

## Tavily Example

```python
import requests

response = requests.post(
    "http://127.0.0.1:8080/tavily/search",
    headers={"Authorization": "Bearer relay_xxx"},
    json={
        "query": "latest AI search APIs",
        "max_results": 3,
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())
```

## Body Key Compatibility Example

```python
import requests

response = requests.post(
    "http://127.0.0.1:8080/exa/search",
    json={
        "api_key": "relay_xxx",
        "query": "latest AI search APIs",
        "numResults": 3,
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())
```

## Brave Example (GET)

```python
import requests

response = requests.get(
    "http://127.0.0.1:8080/brave/search",
    params={"q": "latest AI search APIs", "count": 10},
    headers={"Authorization": "Bearer relay_xxx"},
    timeout=60,
)

response.raise_for_status()
print(response.json())
```

## Jina Reader Example (GET, path-style)

```python
import requests

response = requests.get(
    "http://127.0.0.1:8080/jina/reader/https://example.com/article",
    headers={
        "Authorization": "Bearer relay_xxx",
        "X-Return-Format": "markdown",
    },
    timeout=60,
)

response.raise_for_status()
print(response.text)
```

## Jina Rerank Example (POST)

```python
import requests

response = requests.post(
    "http://127.0.0.1:8080/jina/rerank",
    headers={"Authorization": "Bearer relay_xxx"},
    json={
        "model": "jina-reranker-v2-base-multilingual",
        "query": "latest AI search APIs",
        "documents": ["doc1", "doc2"],
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())
```

## Routing And Load Balancing

Each relay API key can bind multiple groups per provider. For an `/exa/*` request, Search Relay only considers Exa groups bound to that relay key. For a `/tavily/*` request, it only considers Tavily groups. The same strict isolation applies to `/brave/*` (Brave groups only) and `/jina/*` (Jina groups only).

Group selection:

1. Ignore groups not bound to the relay key for that provider.
2. Ignore disabled groups.
3. Ignore groups without available upstream keys.
4. Prefer the eligible group with the fewest requests today for that provider.
5. Break ties by remaining upstream quota and group id.

Inside the selected group, upstream keys are ordered by remaining quota, use count, last-used time, and id.

If retryable upstream errors occur, Search Relay tries another usable key in the selected group, then another eligible group.

## SOCKS5 Proxies

Groups can define a default upstream proxy:

```text
socks5://127.0.0.1:1080
socks5h://proxy.example.com:1080
```

If a request is routed through a group with a proxy, the upstream provider request uses that proxy. If no proxy is set, the upstream request is direct.

## Search Cache

Search cache applies to successful provider-native responses on cacheable routes:

- `/exa/search`
- `/tavily/search`
- `/brave/search`

The cache key includes:

- Provider.
- Endpoint.
- Selected provider group id.
- Sanitized query string.
- Request body.

This means different groups do not share cache entries. This is a
provider-native request cache only — retrieval-level caches (normalized
candidates, deduped results, RRF lists, evidence) belong to the downstream
retrieval application.

Bypass cache:

```text
?no_cache=true
```

Cache headers:

```text
X-Search-Relay-Cache: miss
X-Search-Relay-Cache: hit
```

## Error Format

```json
{
  "error": {
    "code": "relay_auth_failed",
    "message": "Invalid relay key"
  }
}
```

Common errors:

| HTTP status | Code | Meaning |
| --- | --- | --- |
| 401 | `relay_auth_failed` | Relay key is missing or invalid |
| 403 | `relay_key_disabled` | Relay key is disabled |
| 403 | `provider_group_unassigned` | Relay key has no group for this provider |
| 403 | `group_disabled` | All bound groups for this provider are disabled |
| 404 | `unsupported_route` | Provider endpoint is not supported |
| 405 | `unsupported_method` | Route exists but does not allow this HTTP method |
| 429 | `daily_limit_exceeded` | Relay key daily limit is exhausted |
| 503 | `provider_unavailable` | Provider is disabled or no upstream key is available |
| 504 | `upstream_timeout` | Upstream request timed out |

Non-retryable upstream errors may be returned directly to the client.
