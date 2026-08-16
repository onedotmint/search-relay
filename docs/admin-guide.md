# Admin Guide

The admin console is available at:

```text
http://127.0.0.1:8080/admin
```

Search Relay has one administrator login and no user account system.

## Dashboard

The dashboard shows:

- Requests today.
- Success rate.
- Average latency.
- Provider usage.
- Recent failures.

## Groups

Groups are provider-specific routing pools.

Fields:

- `Platform`: `exa` or `tavily`.
- `Group Name`: admin label.
- `Enabled`: whether this group can receive traffic.
- `SOCKS5 Proxy`: optional upstream proxy URL.

Proxy examples:

```text
socks5://127.0.0.1:1080
socks5h://proxy.example.com:1080
```

Use `socks5h://` when DNS resolution should happen through the proxy.

## Upstream Keys

Upstream Keys are real upstream API keys from Exa, Tavily, Brave, or Jina.

Fields:

- `Provider`: upstream provider.
- `Group`: matching provider group.
- `Label`: admin-friendly name.
- `API Key`: upstream key.
- `Quota`: local quota ceiling used by Search Relay.
- `Enabled`: whether the key can be selected.

Search Relay can mark keys invalid or exhausted based on upstream responses. Tavily keys can sync usage from Tavily's usage endpoint.

## API Keys

API Keys are downstream relay keys for clients.

Fields:

- `Label`: admin-friendly name.
- `Exa Groups`: zero or more Exa groups.
- `Tavily Groups`: zero or more Tavily groups.
- `Daily Limit`: optional request limit.
- `Enabled`: whether clients can use the key.

If a key has no groups for a provider, requests to that provider return `provider_group_unassigned`.

## Logs

Logs show request metadata:

- Time.
- Provider.
- Endpoint.
- Relay key label.
- Selected provider group.
- HTTP status.
- Latency.
- Request and response size.
- Error code and message.

Logs do not store upstream API keys or relay key values.

## Cache Center

The cache center lets admins:

- Enable or disable search cache.
- Set cache TTL in seconds.
- Set maximum cache rows.
- Inspect cache entries.
- Delete individual entries.
- Prune expired entries.
- Clear all cache entries.

Search cache is isolated by provider group.

## Settings

Use Settings to change the admin password. Password changes require the current password.

Use a strong password and rotate it if admin access may have been exposed.
