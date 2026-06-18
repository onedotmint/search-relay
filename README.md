# Search Relay

Search Relay is a lightweight admin-managed gateway for search APIs such as Exa and Tavily. It lets you manage upstream provider keys, create downstream relay keys, isolate traffic with provider-specific groups, route traffic through optional SOCKS5 proxies, inspect logs, and cache search responses.

It is designed for teams or individuals who want a simple search API relay with a web admin panel, without building a full user account system.

## Features

- Single administrator login; no end-user account system.
- Provider-specific relay endpoints: `/exa/*` and `/tavily/*`.
- External relay keys that can bind multiple groups per provider.
- Group-level load balancing and optional `socks5://` or `socks5h://` upstream proxy settings.
- Upstream key pools with quota tracking, failure marking, and retry switching.
- Tavily usage sync for upstream keys.
- Search response cache management.
- Request logs with provider, endpoint, relay key, selected group, status, latency, size, and errors.
- React + Ant Design admin console.
- Python FastAPI backend and SQLite persistence.
- Docker Compose deployment.

## How It Works

Search Relay separates three concepts:

- **Groups** isolate provider-specific traffic. A group belongs to exactly one provider, such as Exa or Tavily, and may define a default SOCKS5 proxy.
- **Platform Keys** are real upstream API keys for Exa or Tavily. Each platform key belongs to one matching provider group.
- **API Keys** are relay keys that you give to downstream clients. A relay key can bind multiple Exa groups and multiple Tavily groups.

When a client calls `/exa/search`, Search Relay authenticates the relay key, chooses an eligible Exa group from that key's bindings, selects an upstream Exa key from that group, and forwards the request to Exa. `/tavily/*` follows the same model for Tavily.

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

## Configuration

Copy `.env.example` to `.env` and change the secrets before exposing the service:

```env
APP_DATABASE_PATH=/app/data/search-relay.sqlite3
APP_SECRET_KEY=replace-with-openssl-rand-hex-32
ADMIN_PASSWORD=replace-on-first-deploy
UPSTREAM_TIMEOUT_SECONDS=60
SEARCH_CACHE_ENABLED=true
SEARCH_CACHE_TTL_SECONDS=43200
SEARCH_CACHE_MAX_ROWS=10000
```

Production recommendations:

- Use a long random `APP_SECRET_KEY`.
- Change the admin password after first login.
- Put Search Relay behind HTTPS.
- Do not share upstream Exa or Tavily keys with downstream clients.
- Rotate any key that may have appeared in logs or chat.
- Back up the SQLite database before upgrades.

## Roadmap

- Additional search providers.
- Optional request rate limits per relay key.
- More usage synchronization adapters.
- Configurable group selection policies.

## License

MIT License. See [LICENSE](LICENSE).
