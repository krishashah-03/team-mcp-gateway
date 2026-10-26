# Team MCP Gateway

One MCP endpoint for a whole team. An admin connects MCP servers once; every team member
uses all of them through a single gateway URL (from Claude Code, Cursor, MCP Inspector) or
through a chat agent. The first data source is a WooCommerce store.

> **Status:** backend, React dashboard and Docker packaging are complete and tested. Not yet built:
> Helm charts / Kubernetes deployment.

```
 MCP clients (Claude Code, Cursor, Inspector)         Dashboard (React) / chat
            |  Bearer gwk_...                                |  session token
            v                                                v
        POST /mcp  /lite/mcp                          /api/v1/*  (admin API, chat)
   +------------------------------------- Gateway (FastAPI) -------------------------------------+
   |  auth -> permissions -> routing -> timeout -> audit log + metrics                             |
   +---------+---------------------------+---------------------------+-----------------------------+
             | MCP over Streamable HTTP  |                           |
             v                           v                           v
     woocommerce server            utilities server            docs-search server        ... any server
        |  REST API
        v
   WooCommerce store                         PostgreSQL: users, hashed keys, servers, audit, chats
```

Nothing but the gateway talks to the upstream servers. Upstream credentials never leave it.

## Repository layout

| Path | What it is |
|---|---|
| `frontend/` | Dashboard (React, TypeScript, Vite) and its nginx config |
| `backend/` | Gateway and admin API (FastAPI, SQLAlchemy, Alembic) |
| `backend/app/gateway/` | Upstream connector, tool aggregation, `/mcp` and `/lite/mcp` endpoints |
| `backend/app/chat/` | Chat agent: tool loop and the Gemini adapter |
| `mcp-servers/woocommerce/` | WooCommerce MCP server (written for this project; REST API) |
| `mcp-servers/utilities/` | Time, unit conversion, safe calculator |
| `mcp-servers/docs-search/` | Search and read a folder of documents (sample store policies included) |
| `compose.yaml` | PostgreSQL for local development |
| `compose.full.yaml` | The whole stack in Docker, layered on `compose.yaml` |
| `docs/` | Docker guide, system design and tradeoffs, threat model |

## Run it locally (Windows / PowerShell)

Prerequisites: Python 3.13, Docker Desktop, a WooCommerce site (for example Local) served over
**HTTPS**. WooCommerce ignores API keys sent over plain HTTP.

```powershell
# 1. Database (host port 5433; use 127.0.0.1 not "localhost", which hangs on Windows via IPv6)
$env:POSTGRES_PASSWORD = "choose-one"      # or put it in a .env next to compose.yaml
docker compose up -d

# 2. Backend dependencies and schema
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
```

Create `backend/.env` (never commit it):

```
DATABASE_URL=postgresql+psycopg://mcp_app:<POSTGRES_PASSWORD>@127.0.0.1:5433/team_mcp_gateway
JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
CREDENTIAL_ENCRYPTION_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
BOOTSTRAP_ADMIN_EMAIL=you@example.com
BOOTSTRAP_ADMIN_PASSWORD=<at least 10 characters>

# Upstream servers registered automatically at startup (the token is that server's MCP_AUTH_TOKEN)
SEED_WOOCOMMERCE_URL=http://127.0.0.1:8001/mcp
SEED_WOOCOMMERCE_TOKEN=<same value as the WooCommerce server's MCP_AUTH_TOKEN>
SEED_UTILITIES_URL=http://127.0.0.1:8003/mcp
SEED_DOCS_URL=http://127.0.0.1:8004/mcp

GEMINI_API_KEY=<optional, enables the chat agent>
```

Create `mcp-servers/woocommerce/.env`:

```
WOOCOMMERCE_BASE_URL=https://your-store.local
WOOCOMMERCE_CONSUMER_KEY=ck_...
WOOCOMMERCE_CONSUMER_SECRET=cs_...
# Only when the store uses a private CA (Local): path to the site's .crt file
WOOCOMMERCE_CA_BUNDLE=C:\path\to\your-store.local.crt
MCP_AUTH_TOKEN=<random secret shared with the gateway>
```

Start the four services, each in its own terminal (use the backend's venv Python):

```powershell
cd mcp-servers\woocommerce; ..\..\backend\.venv\Scripts\python.exe -m woocommerce_mcp   # :8001
cd mcp-servers\utilities;   ..\..\backend\.venv\Scripts\python.exe -m utilities_mcp     # :8003
cd mcp-servers\docs-search; ..\..\backend\.venv\Scripts\python.exe -m docs_search_mcp   # :8004
cd backend;                 .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Then the dashboard (it proxies `/api` and `/mcp` to the gateway on port 8000):

```powershell
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173  (sign in with the bootstrap admin)
```

API docs: http://127.0.0.1:8000/docs. Use `127.0.0.1`, not `0.0.0.0`, in a browser. If a port is
"already in use", an earlier copy is still running: `netstat -ano | findstr :8000`, then
`Stop-Process -Id <pid> -Force`.

### Settings that have defaults

| Setting | Default | Meaning |
|---|---|---|
| `UPSTREAM_ALLOW_PRIVATE_NETWORKS` | `true` | Allow loopback/private upstream URLs. **Set `false` in production** and list expected hosts in `UPSTREAM_ALLOWED_HOSTS` (e.g. `*.svc.cluster.local`). Cloud-metadata addresses are always blocked. |
| `UPSTREAM_CALL_TIMEOUT_SECONDS` / `UPSTREAM_LIST_TIMEOUT_SECONDS` | `15` / `5` | Per-call and per-tool-listing timeouts |
| `TOOL_CACHE_TTL_SECONDS` | `10` | How long a server's tool list is cached |
| `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_SECONDS` | `5`, `900` | Login throttling per client and email |
| `RATE_LIMIT_PER_MINUTE` | `120` | Requests per user on `/mcp` and `/chat`; `0` disables |
| `MAX_BODY_BYTES` | `1048576` | Request size cap |
| `GATEWAY_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*` | Host headers accepted on `/mcp`; add your public host |
| `CORS_ORIGINS` | `http://localhost:5173` | Allowed dashboard origins |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Chat model |
| `CHAT_MAX_STEPS`, `CHAT_MAX_DIRECT_TOOLS` | `6`, `40` | Tool-loop limit; above this many tools the agent switches to search-then-run |
| `LOG_FORMAT`, `LOG_LEVEL` | `text`, `INFO` | `json` for log collectors |
| `METRICS_TOKEN` | unset | Scrape token for `/metrics` (otherwise an admin session is required) |

## Use it

**Place an order:** open the store, add a product to the cart, check out with cash on delivery,
and confirm it in WooCommerce admin.

**Connect an external MCP client.** Sign in at `POST /api/v1/auth/login`, create a key with
`POST /api/v1/keys`, then point the client at the gateway:

```
URL:    http://127.0.0.1:8000/mcp          (every tool)
        http://127.0.0.1:8000/lite/mcp     (search_tools / describe_tool / run_tool only)
Header: Authorization: Bearer gwk_...
```

MCP Inspector: `npx @modelcontextprotocol/inspector`, choose *Streamable HTTP*, set the URL and header.
Tool names are prefixed with their server: `woocommerce__list_orders`, `utilities__calculate`.

**Chat agent:** set `GEMINI_API_KEY`, restart the gateway, then `POST /api/v1/chat`
`{"message": "what orders came in today?"}`. The response lists the tools it called. It acts as the
signed-in user, so its calls are checked against that user's permissions and audited under their name.

**Restrict tools:** `POST /api/v1/permissions`, for example
`{"role": "member", "server_slug": "woocommerce", "tool_name": "create_product", "effect": "deny"}`.
Hidden from listings and rejected if called directly. User rules beat role rules; tool rules beat
server-wide rules; deny beats allow at equal specificity; no rule means allowed.

## Run it all in Docker

```powershell
docker compose -f compose.yaml -f compose.full.yaml up -d --build     # dashboard on http://127.0.0.1:8080
```

See [docs/docker.md](docs/docker.md) for the required settings, how the containers and networks are
arranged, and how to point it at a store running on your machine.

## Test it

| Suite | Command | Tests |
|---|---|---|
| Gateway (unit, database, HTTP integration) | `cd backend; python -m pytest` | 298 |
| WooCommerce MCP server | `cd mcp-servers/woocommerce; python -m pytest` | 65 |
| Utilities MCP server | `cd mcp-servers/utilities; python -m pytest` | 52 |
| Docs-search MCP server | `cd mcp-servers/docs-search; python -m pytest` | 25 (1 skipped without symlink rights) |
| Dashboard (components, flows, API client) | `cd frontend; npm test` | 109 |
| Dashboard in a real browser, real gateway | `cd frontend; npm run test:e2e` | 12 |
| The containerized stack, through nginx | `cd frontend; npm run test:e2e:stack` (see docs/docker.md) | 5 |

Use the backend's virtualenv Python for the Python suites. The gateway suite needs PostgreSQL running;
its database tests roll back and its HTTP tests delete everything they create. The browser suite starts
its own gateway (port 8010), a fixture MCP server and the dashboard, creates a throwaway admin directly
in the database, and removes everything afterwards; it sets an empty `GEMINI_API_KEY`, so it never calls
a model. `backend/tests/e2e_smoke.py` is an additional end-to-end script for a *running* system including
the real WooCommerce store: start the gateway and the WooCommerce server, then run it.

## Docs

- [Running in Docker](docs/docker.md)
- [System design and tradeoffs](docs/system-design.md)
- [Threat model](docs/threat-model.md)

## Not done yet

Helm charts and Kubernetes/k3s deployment (the images, health checks, non-root users and read-only
filesystems are ready for it), OAuth on the gateway, multi-store provisioning, and a real-model test of
the chat agent (it is tested with a scripted model and a mocked Gemini API). Rate limits and the tool
cache are per process, which matters once the gateway runs more than one replica (see the design note).
