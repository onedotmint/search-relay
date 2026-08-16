# Smart Search Integration

This guide explains how a downstream retrieval application (referred to here as
**Smart Search**) consumes Search Relay as its Provider Access Gateway.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Smart Search (client)                          │
│                                                                      │
│  Retrieval intelligence: policy, normalization, dedup, RRF,           │
│  rerank orchestration, evidence. Never holds provider credentials.    │
└──────────────────────────────────────────────────────────────────────┘
                          │  SEARCH_RELAY_API_KEY = relay_xxx
                          │  Authorization: Bearer relay_xxx
                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        Search Relay (gateway)                         │
│                                                                      │
│  Provider-native relay, credentials & key pools, group routing,       │
│  quota, retry/failover, proxy, operational logs, admin UI.            │
└──────────────────────────────────────────────────────────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────────┐
   │  Brave  │    │   Exa   │    │ Tavily  │    │    Jina      │
   │  search │    │  search │    │  search │    │  reader /    │
   │  (GET)  │    │  (POST) │    │  (POST) │    │  rerank      │
   └─────────┘    └─────────┘    └─────────┘    └──────────────┘
```

Smart Search never talks to Brave/Exa/Tavily/Jina directly. It calls Search
Relay with a single relay credential (`relay_xxx`); Search Relay substitutes
the real upstream credential from its key pools and passes the provider-native
response back byte-for-byte.

Jina flow inside Search Relay: `GET /jina/reader/<url>` proxies the Jina
Reader (`https://r.jina.ai/<url>`), and `POST /jina/rerank` proxies the Jina
Reranker (`https://api.jina.ai/v1/rerank`). Jina is a Reader + Reranker
upstream only — Search Relay does not expose a Jina "search" route.

## Ownership Split

| Concern | Owner |
| --- | --- |
| Retrieval policy (which providers to query, when) | Smart Search |
| Multi-provider search orchestration / parallel fan-out | Smart Search |
| Candidate normalization & URL canonicalization | Smart Search |
| Deduplication, RRF, result fusion, cross-provider ranking | Smart Search |
| Rerank orchestration (which docs to rerank, with which model) | Smart Search |
| Content quality scoring, query rewriting, semantic ranking | Smart Search |
| Evidence assembly | Smart Search |
| Provider-native API relay (`/exa/*`, `/tavily/*`, `/brave/*`, `/jina/*`) | Search Relay |
| Upstream credential storage & key pools (rotation, isolation) | Search Relay |
| Provider group routing & quota | Search Relay |
| Retry / failover between upstream keys | Search Relay |
| SOCKS5 proxy routing | Search Relay |
| Operational logs & admin UI | Search Relay |

Search Relay is strictly the Provider Access Layer. It does **not** implement
unified search results, query routing, retrieval fusion, RRF, URL
deduplication, or rerank orchestration — all of that stays in Smart Search.

## Configuration

In Smart Search, configure one relay credential:

```env
SEARCH_RELAY_BASE_URL=https://relay.example.com
SEARCH_RELAY_API_KEY=relay_xxx
```

Every adapter authenticates with the same relay key:

```http
Authorization: Bearer relay_xxx
```

Real provider API keys never appear in Smart Search configuration — Search
Relay owns and injects them.

## Adapter Base-URL Mapping

Point each provider adapter's base URL at the corresponding Search Relay
route namespace:

| Provider adapter | Search Relay base URL | Method | Notes |
| --- | --- | --- | --- |
| Brave | `{BASE}/brave` | GET | e.g. `GET {BASE}/brave/search?q=...&count=10` |
| Exa | `{BASE}/exa` | POST | e.g. `POST {BASE}/exa/search` |
| Tavily | `{BASE}/tavily` | POST | e.g. `POST {BASE}/tavily/search` |
| Jina Reader | `{BASE}/jina/reader` | GET | target URL as path suffix: `GET {BASE}/jina/reader/https://example.com/article` |
| Jina Rerank | `{BASE}/jina/rerank` | POST | JSON body `{"model", "query", "documents"}` |

Example (`{BASE}` = `SEARCH_RELAY_BASE_URL`):

```python
import requests

# Brave search (GET; query params forwarded)
response = requests.get(
    f"{SEARCH_RELAY_BASE_URL}/brave/search",
    params={"q": "latest AI search APIs", "count": 10},
    headers={"Authorization": f"Bearer {SEARCH_RELAY_API_KEY}"},
    timeout=60,
)

# Exa search (POST; provider-native body forwarded as-is)
response = requests.post(
    f"{SEARCH_RELAY_BASE_URL}/exa/search",
    headers={"Authorization": f"Bearer {SEARCH_RELAY_API_KEY}"},
    json={"query": "latest AI search APIs", "numResults": 3},
    timeout=60,
)

# Jina Rerank (POST; JSON body forwarded)
response = requests.post(
    f"{SEARCH_RELAY_BASE_URL}/jina/rerank",
    headers={"Authorization": f"Bearer {SEARCH_RELAY_API_KEY}"},
    json={"model": "jina-reranker-v2-base-multilingual", "query": "ai", "documents": ["doc1", "doc2"]},
    timeout=60,
)

# Jina Reader (GET; target URL as path suffix, optional query params forwarded)
response = requests.get(
    f"{SEARCH_RELAY_BASE_URL}/jina/reader/https://example.com/article",
    headers={"Authorization": f"Bearer {SEARCH_RELAY_API_KEY}", "X-Return-Format": "markdown"},
    timeout=60,
)
```

Relay requests may alternatively carry the relay key in the JSON body
(`api_key` / `apiKey`) or as a query parameter (`api_key` / `apiKey`); Search
Relay strips it and never forwards it upstream. The `Authorization` header is
the recommended mode.

## Integration Boundaries

- **Credentials**: relay keys (`relay_xxx`) are for downstream clients such as
  Smart Search. Upstream provider credentials live only inside Search Relay's
  admin (Upstream Keys) and are never exposed to clients.
- **Caching**: Search Relay's cache is a **provider-native request cache** —
  it stores exact upstream responses for the same provider + native endpoint +
  native request (currently `/exa/search`, `/tavily/search`, `/brave/search`).
  It is **not** a retrieval cache. Normalized candidates, deduped results,
  RRF-ranked lists, and evidence caches belong to Smart Search.
- **Logs**: Search Relay logs provider, endpoint, relay key, provider group,
  status, latency, byte sizes, error code, and the *internal id* of the
  selected upstream key (never the key value). Retrieval telemetry (query,
  policy, results, dedup, RRF, rerank scores) is Smart Search's domain.
- **Fallback**: Search Relay is a convenience gateway, not an inseparable part
  of Smart Search's provider abstraction. If Search Relay is unavailable,
  Smart Search adapters can be pointed back at the providers directly
  (Brave/Exa/Tavily/Jina) using their own keys — configuration only, no code
  change required.
