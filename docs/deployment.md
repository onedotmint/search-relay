# Deployment Guide

## Docker Compose

Create an environment file:

```bash
cp .env.example .env
nano .env   # set APP_SECRET_KEY / ADMIN_PASSWORD to real random values
```

The example ships with `APP_ENV=production`; production refuses to start
with the placeholder secret/password values, so the copy must be edited
before the first `up`.

Start (builds the image from the local source, then runs it):

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
| `APP_SECRET_KEY` | Session signing key; production refuses to start with a known placeholder value |
| `APP_ENV` | `development` (default) or `production` — production refuses startup with placeholder secrets/passwords (`.env.example` values are public knowledge) |
| `ADMIN_PASSWORD` | Initial admin password; same placeholder guard as the secret key |
| `UPSTREAM_TIMEOUT_SECONDS` | Provider request timeout |
| `MAX_UPSTREAM_ATTEMPTS` | Total upstream attempts per relay request (default 3); caps retries regardless of key-pool size |
| `REQUEST_LOG_RETENTION_DAYS` | Prune request_logs older than N days at startup (default 30) |
| `COOKIE_SECURE` | Set the Secure flag on the admin session cookie; enable (`true`) when the admin UI is served over HTTPS |
| `SEARCH_CACHE_ENABLED` | Default cache enabled state |
| `SEARCH_CACHE_TTL_SECONDS` | Default cache TTL |
| `SEARCH_CACHE_MAX_ROWS` | Default maximum cache rows |

## Security Notes

- `APP_ENV=production` refuses to start when `APP_SECRET_KEY` or
  `ADMIN_PASSWORD` is one of the known placeholder strings shipped in
  `.env.example` (they are public knowledge — the repo is public).
  Generate a real key with `openssl rand -hex 32` and a strong password.

## Operations Checklist

- Change the admin password after first login.
- Add provider groups before adding platform keys.
- Add platform keys to matching provider groups.
- Create relay API keys for clients.
- Rotate upstream provider keys periodically.
- Review logs after provider errors or client reports.
- Back up `data/` before upgrades.
