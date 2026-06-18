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
| Exa | `/exa/search` | Forwards to Exa search |
| Exa | `/exa/contents` | Forwards to Exa contents |
| Exa | `/exa/answer` | Forwards to Exa answer |
| Tavily | `/tavily/search` | Forwards to Tavily search |
| Tavily | `/tavily/extract` | Forwards to Tavily extract |
| Tavily | `/tavily/crawl` | Forwards to Tavily crawl |
| Tavily | `/tavily/map` | Forwards to Tavily map |
| Tavily | `/tavily/research` | Forwards to Tavily research |

Use the provider's native request body for each endpoint.

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

## Routing And Load Balancing

Each relay API key can bind multiple groups per provider. For an `/exa/*` request, Search Relay only considers Exa groups bound to that relay key. For a `/tavily/*` request, it only considers Tavily groups.

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

Search cache applies to successful search responses:

- `/exa/search`
- `/tavily/search`

The cache key includes:

- Provider.
- Endpoint.
- Selected provider group id.
- Sanitized query string.
- Request body.

This means different groups do not share cache entries.

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
| 429 | `daily_limit_exceeded` | Relay key daily limit is exhausted |
| 503 | `provider_unavailable` | Provider is disabled or no upstream key is available |
| 504 | `upstream_timeout` | Upstream request timed out |

Non-retryable upstream errors may be returned directly to the client.
