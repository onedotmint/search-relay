# Contributing

Thanks for considering a contribution to Search Relay.

## Development Setup

Install backend dependencies:

```bash
python -m pip install -e ".[dev]"
```

Install frontend dependencies:

```bash
npm --prefix frontend ci
```

Run backend tests:

```bash
python -m pytest -q
```

Build the frontend:

```bash
npm --prefix frontend run build
```

Run the backend locally:

```bash
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080
```

Run the frontend dev server:

```bash
npm --prefix frontend run dev
```

## Pull Requests

For behavior changes:

- Add or update backend tests.
- Keep migrations additive when possible.
- Keep public API behavior documented in `docs/api.md`.
- Run `python -m pytest -q`.
- Run `npm --prefix frontend run build` when frontend code changes.

For documentation changes:

- Use example values only.
- Do not include real keys, relay keys, IP addresses, passwords, tokens, or proxy credentials.

## Code Style

- Prefer small, focused backend helpers over large route handlers.
- Keep provider-specific behavior in provider adapters where possible.
- Keep frontend UI consistent with Ant Design controls.
- Do not edit generated `app/static_admin` assets by hand.

## Security

Never submit real `.env` files, databases, upstream keys, relay keys, admin passwords, or proxy credentials. See [SECURITY.md](SECURITY.md).
