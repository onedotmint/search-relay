# Security Policy

## Supported Model

Search Relay uses a single administrator authentication model. It does not provide end-user accounts or tenant isolation. Treat the admin console as a privileged operations surface.

The service stores:

- Admin password hash.
- Upstream provider API keys.
- Downstream relay keys.
- Request metadata logs.
- Search cache entries.

## Secret Handling

Do not commit `.env`, SQLite databases, upstream API keys, relay keys, proxy credentials, or admin passwords.

Before exposing a deployment:

- Set a long random `APP_SECRET_KEY`.
- Set a strong `ADMIN_PASSWORD`.
- Change the admin password after first login.
- Use HTTPS through a reverse proxy or managed load balancer.
- Restrict access to `/admin` where possible.
- Rotate upstream keys if they may have been exposed.

## Proxy Credentials

Group-level SOCKS5 proxies may include credentials in the URL. Store them only in the database through the admin panel. Do not place real proxy credentials in screenshots, examples, issues, or logs.

## Reporting Vulnerabilities

Please do not open a public issue for a sensitive vulnerability. Use a private channel with the maintainer if available. If no private channel is available, open a minimal issue that asks for a security contact without disclosing exploit details.

Include:

- Affected version or commit.
- Impact.
- Reproduction steps.
- Suggested mitigation, if known.

## Rotation Guidance

If a relay key, upstream key, admin password, or proxy password is exposed:

1. Disable or delete the exposed key in the admin console.
2. Rotate the upstream provider key at the provider.
3. Change the admin password if admin access may be affected.
4. Review request logs for suspicious traffic.
5. Clear search cache entries if cached payloads may contain sensitive data.
