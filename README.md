# Search Relay

Search Relay is a lightweight admin-managed gateway for search APIs such as Exa, Tavily, Brave, and Jina. It lets you manage upstream provider keys, create downstream relay keys, isolate traffic with provider-specific groups, route traffic through optional SOCKS5 proxies, inspect logs, and cache provider-native responses.

It is designed for teams or individuals who want a simple search API relay with a web admin panel, without building a full user account system.

## Features

- Single administrator login; no end-user account system.
- Provider-specific relay endpoints: `/exa/*`, `/tavily/*`, `/brave/*`, and `/jina/*` (GET and POST routes).
- External relay keys that can bind multiple groups per provider.
- Group-level load balancing and optional `socks5://` or `socks5h://` upstream proxy settings.
- Upstream key pools with quota tracking, failure marking, and retry switching.
- Tavily usage sync for upstream keys.
- Provider-native response cache for search routes.
- Request logs with provider, endpoint, relay key, selected group, selected upstream key id, status, latency, size, and errors.
- React + Ant Design admin console.
- Python FastAPI backend and SQLite persistence.
- Docker Compose deployment.

## How It Works

Search Relay separates three concepts:

- **Groups** isolate provider-specific traffic. A group belongs to exactly one provider, such as Exa, Tavily, Brave, or Jina, and may define a default SOCKS5 proxy.
- **Upstream Keys** are real upstream API keys for Exa, Tavily, Brave, or Jina. Each upstream key belongs to one matching provider group.
- **API Keys** are relay keys that you give to downstream clients. A relay key can bind multiple groups per provider.

When a client calls `/exa/search`, Search Relay authenticates the relay key, chooses an eligible Exa group from that key's bindings, selects an upstream Exa key from that group, and forwards the request to Exa. `/tavily/*`, `/brave/*`, and `/jina/*` follow the same model for Tavily, Brave, and Jina.

### Key Pool Purpose & Policy

The upstream key pool exists for credential rotation, project/environment isolation, rate-limit management, and legitimate failover: when one key is rate-limited, exhausted, invalid, or slow, the relay rotates to the next eligible key in the group. It is **not** a mechanism for bypassing provider limits, exceeding free tiers, or amplifying quota — every relayed request consumes a real upstream call, and invalid or quota-exhausted keys are excluded from rotation rather than recycled.

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

Open the admin console:

```text
http://127.0.0.1:8080/admin
```

Use `ADMIN_PASSWORD` from `.env` for the first login, then change it in the admin Settings page.

## Basic Relay Usage

Create a relay key in the admin console, bind it to one or more Exa groups, then call:

```python
import requests

response = requests.post(
    "http://127.0.0.1:8080/exa/search",
    headers={"Authorization": "Bearer relay_xxx"},
    json={"query": "latest AI search APIs", "numResults": 3},
    timeout=60,
)
response.raise_for_status()
print(response.json())
```

For Tavily:

```python
import requests

response = requests.post(
    "http://127.0.0.1:8080/tavily/search",
    headers={"Authorization": "Bearer relay_xxx"},
    json={"query": "latest AI search APIs", "max_results": 3},
    timeout=60,
)
response.raise_for_status()
print(response.json())
```

## Documentation

- [API Guide](docs/api.md)
- [Admin Guide](docs/admin-guide.md)
- [Deployment Guide](docs/deployment.md)
- [Smart Search Integration](docs/smart-search-integration.md)
- [Security Policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Development

Backend:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080
```

Frontend:

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
npm --prefix frontend run build
```

Docker builds compile the React admin console and copy the generated files into `app/static_admin`.

## Benchmarks

Run the concurrency + large-payload benchmark against a local mock upstream
(no real provider keys needed):

```bash
python scripts/benchmark.py
```

It reports p50/p95/p99 latency, requests/sec and failures at 1/10/50/100
concurrent relay requests, plus a payload ladder from 100 KB to 20 MB.

**Decision rule:** SQLite (single connection, WAL-free) is the deliberate
architecture for this workload. Only if the benchmark shows failures or
`database is locked` errors should you investigate WAL mode, `busy_timeout`,
or connection management — otherwise do **not** change the database
architecture. Large responses are buffered in memory by design; re-evaluate
only if real payloads consistently exceed the ladder's largest sizes.

## Configuration

Copy `.env.example` to `.env` and change the secrets before exposing the service:

```env
APP_DATABASE_PATH=/app/data/search-relay.sqlite3
APP_SECRET_KEY=replace-with-openssl-rand-hex-32
APP_ENV=development
ADMIN_PASSWORD=replace-on-first-deploy
UPSTREAM_TIMEOUT_SECONDS=60
MAX_UPSTREAM_ATTEMPTS=3
REQUEST_LOG_RETENTION_DAYS=30
COOKIE_SECURE=false
SEARCH_CACHE_ENABLED=true
SEARCH_CACHE_TTL_SECONDS=43200
SEARCH_CACHE_MAX_ROWS=10000
```

Production recommendations:

- Use a long random `APP_SECRET_KEY`; set `APP_ENV=production` so startup
  refuses a default key.
- Set `COOKIE_SECURE=true` when the admin UI is served over HTTPS.
- Change the admin password after first login.
- Put Search Relay behind HTTPS.
- Do not share upstream Exa, Tavily, Brave, or Jina keys with downstream clients.
- Rotate any key that may have appeared in logs or chat.
- Back up the SQLite database before upgrades.

## Caching Boundary

Search Relay caches **provider-native request responses only** — the same
provider + native endpoint + native request (currently `/exa/search`,
`/tavily/search`, and `/brave/search`). This is not a retrieval cache:
normalized candidates, deduped results, RRF-ranked lists, and evidence caches
belong to the downstream retrieval application (see
[docs/smart-search-integration.md](docs/smart-search-integration.md)).

## Roadmap

- Additional search providers.
- Optional request rate limits per relay key.
- More usage synchronization adapters.
- Configurable group selection policies.

## License

MIT License. See [LICENSE](LICENSE).
