# System design and tradeoffs

## Components and responsibilities

| Component | Responsibility |
|---|---|
| **Gateway** (`backend/`) | Authenticates users, decides what each may use, routes tool calls to upstreams, records an audit log, exposes one MCP endpoint and an admin API. |
| **Upstream MCP servers** (`mcp-servers/*`) | Each exposes one data source as MCP tools. They know nothing about users. |
| **PostgreSQL** | Users, hashed API keys, the server registry (with encrypted credentials), permission rules, audit log, chat history. |
| **Chat agent** (`backend/app/chat/`) | Runs a model in a tool loop, calling tools through the gateway as the signed-in user. |

## Why these choices

**The WooCommerce MCP server is separate from the gateway.** The gateway is a generic router; a
store integration is one of many upstreams. Keeping them apart means the gateway never holds store
credentials, the store server can be scaled, replaced or isolated by network policy on its own, and
adding a second store later is just registering another server.

**The WooCommerce server uses the WooCommerce REST API.** It needs no shell or database access to
the WordPress pod (WP-CLI would), it works against any WooCommerce site including a remote one, and
it uses WooCommerce's own permission model through a scoped key. The cost is HTTP latency and the
requirement that the store be served over HTTPS, because WooCommerce ignores Basic-auth keys over
plain HTTP.

**Short-lived upstream sessions.** Every list or call opens a session, does its work and closes it.
That keeps the gateway stateless (any replica can serve any request), and a dead upstream can never
leave a half-open connection behind. The price is an extra handshake per call, a few milliseconds
against a local upstream. If that matters, a connection pool per upstream is the next step.

**Stateless `/mcp`.** The MCP endpoint holds no per-client session, again so replicas are
interchangeable.

**Sync SQLAlchemy in worker threads.** Database work is short and runs in a thread pool, which
keeps the code simple and the event loop free. Moving to async SQLAlchemy would not change behaviour.

**Layering.** `api` (HTTP) -> `services` (rules) -> `repositories` (SQL). Services only flush; the
caller owns the transaction, which is also what lets the database tests roll back cleanly.

## Tool aggregation, routing and failure handling

1. **Naming.** A tool `list_orders` on server `woocommerce` is exposed as `woocommerce__list_orders`.
   Server slugs are lowercase letters, digits and single hyphens, so they never contain `__`; splitting
   on the first `__` is unambiguous. Two servers may therefore expose the same tool name. Names longer
   than 64 characters (a common client limit) are skipped and logged, and slugs are capped at 40.
2. **Listing and health.** A background task probes every enabled server every 10 s, in parallel, each
   probe bounded by a 5 s timeout, and keeps the latest result (tools or the reason for failure). Requests
   read that result instead of probing, so a dead server never makes the dashboard, a tool listing or a
   call to another server wait. A server that fails is simply absent from tool lists and shows as
   unreachable with a reason. Only a server with no result yet, one whose URL or credential just changed,
   or an explicit "re-check" is probed inline. Without the background task (`HEALTH_CHECK_INTERVAL_SECONDS=0`)
   results expire after 10 s and are re-probed on demand.
3. **Calling.** The server list is re-read from the database on every call, so disabling or removing
   a server takes effect immediately regardless of caches. Then, in order: permission check, a check
   that the upstream really lists that tool (one forced re-probe if it is not in the cache), the call
   with a 15 s timeout, and an audit record.
4. **Failure modes** all return a normal MCP tool result with `isError` set and a short message
   naming the server and the reason (timeout, unreachable, credential rejected, blocked by network
   policy, unknown tool, not permitted). Nothing an upstream says is placed in an error message; only
   the exception type is reported.
5. **Many tools.** `/lite/mcp` exposes only `search_tools`, `describe_tool` and `run_tool`, so a
   model is not handed hundreds of definitions. The chat agent switches to the same approach above
   40 visible tools. Ranking is keyword-based (name hits count triple); embeddings would be the upgrade.

## Identity, credentials and permissions

- **Dashboard users** sign in with email and password (scrypt) and receive a short-lived JWT. The
  token carries only the user id; the role and active flag are read from the database on every
  request, so demotion or disabling is immediate.
- **Gateway API keys** (`gwk_<id>_<secret>`) belong to a user, are shown once, stored as a SHA-256
  hash (the secret is 256 bits of randomness, so a fast hash is appropriate), and can be revoked or
  expire. A key is only valid while its owner is active.
- **Upstream credentials** are encrypted with Fernet. No API returns them; only `has_credential`.
- **Permissions** are allow/deny rules per role or per user, per server or per tool (see README).

## The dashboard

A React single-page app, served by nginx, which is also the single public entry point: it forwards
`/api`, `/mcp` and `/lite` to the gateway, so browsers and MCP clients use one origin (no CORS needed).
The session token lives in `sessionStorage`, so it ends with the tab. The token is held in JavaScript
rather than an HttpOnly cookie because the API is token-based for non-browser clients too; the strict
Content-Security-Policy (no inline scripts, no third-party origins) is what limits XSS, and all message
and tool text is rendered as plain text, never as HTML. Role checks in the UI only decide what to show;
the API enforces them. An API key is displayed once, in a dialog that is gone when closed. Statuses
refresh every 15 s and a "Re-check all" button asks the gateway to probe immediately.

## What changes for production

| Concern | Local | Production |
|---|---|---|
| Database | Docker Postgres on `127.0.0.1:5433` | Managed Postgres or an operator; connection string from a Secret |
| Secrets | `.env` files | Kubernetes Secrets: `JWT_SECRET`, `CREDENTIAL_ENCRYPTION_KEY`, DB URL, upstream tokens, `GEMINI_API_KEY` |
| Upstream URLs | `http://127.0.0.1:800x/mcp` | In-cluster Service DNS names; `UPSTREAM_ALLOW_PRIVATE_NETWORKS=false` plus an allowlist such as `*.svc.cluster.local` |
| Public hosts | `localhost` | `GATEWAY_ALLOWED_HOSTS` and `CORS_ORIGINS` set to the real hostnames; TLS at the ingress |
| Store | Local by Flywheel with a private CA | A real certificate; leave `WOOCOMMERCE_CA_BUNDLE` unset |
| Upstream exposure | any port | In Docker: a separate network the dashboard is not on, no published ports. In Kubernetes: ClusterIP only plus a NetworkPolicy allowing only the gateway. The shared bearer token is a second layer either way |
| Migrations | `alembic upgrade head` by hand | A Helm pre-upgrade Job |
| Logs / metrics | text, `/metrics` for admins | `LOG_FORMAT=json`, Prometheus scraping with `METRICS_TOKEN` |

## Known limits (deliberate simplifications)

- **Rate limits and the tool cache are per process.** With N gateway replicas the effective limit is
  N times higher and each replica probes upstreams itself. A shared store (Redis) would make both global.
- **Login throttling** keys on the connecting address; behind a proxy, configure the proxy so the real
  client address reaches the app (or key on a trusted forwarded header).
- **Audit writes are best effort**: a database error is logged but does not fail the user's call.
- **No refresh tokens or logout list** for dashboard sessions; tokens simply expire.
- **Chat history** is stored in plain text per user.
- **Keyword tool search** rather than semantic search.
