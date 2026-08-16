# Deployment Guide

## Docker Compose

Create an environment file:

```bash
cp .env.example .env
```

Edit `.env`:

```env
APP_HOST=0.0.0.0
APP_PORT=8080
APP_DATABASE_PATH=/app/data/search-relay.sqlite3
APP_SECRET_KEY=replace-with-openssl-rand-hex-32
ADMIN_PASSWORD=replace-on-first-deploy
UPSTREAM_TIMEOUT_SECONDS=60
SEARCH_CACHE_ENABLED=true
SEARCH_CACHE_TTL_SECONDS=43200
SEARCH_CACHE_MAX_ROWS=10000
```

Start:

```bash
docker compose up -d --build
```

Verify:

```bash
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health').read().decode())"
```

Open:

```text
http://127.0.0.1:8080/admin
```

## Reverse Proxy

For production, place Search Relay behind HTTPS.

Example upstream target:

```text
http://127.0.0.1:8080
```

Public URL example:

```text
https://your-domain.example
```

Recommended:

- Terminate TLS at the reverse proxy.
- Restrict `/admin` by IP allowlist or private network when possible.
- Keep `/exa/*` and `/tavily/*` available only to trusted clients.
- Set reasonable body size and timeout limits.

## Backups

The default Docker Compose setup stores SQLite data in:

```text
./data
```

Back up this directory before upgrades:

```bash
docker compose down
cp -a data data.backup
docker compose up -d
```

## Upgrades

```bash
git pull
docker compose up -d --build
```

Database migrations are additive and run during application startup.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `APP_HOST` | Bind host for local non-Docker runs |
| `APP_PORT` | Published Docker port |
| `APP_DATABASE_PATH` | SQLite database path |
| `APP_SECRET_KEY` | Session signing key (required for production; startup refuses a default key in production) |
| `APP_ENV` | `development` (default) or `production` — production + default secret refuses startup |
| `ADMIN_PASSWORD` | Initial admin password |
| `UPSTREAM_TIMEOUT_SECONDS` | Provider request timeout |
| `MAX_UPSTREAM_ATTEMPTS` | Total upstream attempts per relay request (default 3); caps retries regardless of key-pool size |
| `REQUEST_LOG_RETENTION_DAYS` | Prune request_logs older than N days at startup (default 30) |
| `COOKIE_SECURE` | Set the Secure flag on the admin session cookie; enable (`true`) when the admin UI is served over HTTPS |
| `SEARCH_CACHE_ENABLED` | Default cache enabled state |
| `SEARCH_CACHE_TTL_SECONDS` | Default cache TTL |
| `SEARCH_CACHE_MAX_ROWS` | Default maximum cache rows |

## Security Notes

- `APP_ENV=production` with the default `APP_SECRET_KEY` refuses to start —
  generate a strong key with `openssl rand -hex 32`.
- Set `COOKIE_SECURE=true` whenever the admin UI is reachable over HTTPS
  (any deployment behind a reverse proxy terminating TLS).

## Operations Checklist

- Change the admin password after first login.
- Add provider groups before adding platform keys.
- Add platform keys to matching provider groups.
- Create relay API keys for clients.
- Rotate upstream provider keys periodically.
- Review logs after provider errors or client reports.
- Back up `data/` before upgrades.
