# Running everything in Docker

Two compose files:

| File | What it starts |
|---|---|
| `compose.yaml` | PostgreSQL only, for development (the backend and servers run on your machine) |
| `compose.full.yaml` | The whole application, layered on top: gateway, dashboard, and the three MCP servers |

```powershell
# development database, as before
docker compose up -d

# the full stack
docker compose -f compose.yaml -f compose.full.yaml up -d --build
```

The dashboard is then at **http://127.0.0.1:8080** (change with `WEB_PORT`). It is the only published
port. MCP clients connect to the same origin: `http://127.0.0.1:8080/mcp` with
`Authorization: Bearer gwk_...`.

## Settings

Compose reads variables from the shell environment or from a `.env` file next to `compose.yaml`
(never commit it). Variables marked *required* make `docker compose up` stop with a message if missing.

| Variable | Required | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | Database password |
| `JWT_SECRET` | yes | At least 32 random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CREDENTIAL_ENCRYPTION_KEY` | yes | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `WOOCOMMERCE_BASE_URL`, `WOOCOMMERCE_CONSUMER_KEY`, `WOOCOMMERCE_CONSUMER_SECRET` | yes | Your store and its REST API key (the store must use HTTPS) |
| `WOOCOMMERCE_MCP_TOKEN`, `UTILITIES_MCP_TOKEN`, `DOCS_MCP_TOKEN` | yes | A random secret per MCP server. The gateway presents it; the server rejects anything else |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` | no | Creates the first admin on first start, only when no users exist |
| `GEMINI_API_KEY` | no | Enables the chat assistant |
| `PUBLIC_HOSTS` | no | Host names clients use, e.g. `gateway.example.com:*`. Default `localhost:*,127.0.0.1:*` |
| `WEB_PORT` | no | Published dashboard port (default 8080) |
| `STORE_HOSTNAME`, `WOOCOMMERCE_CA_BUNDLE` | no | For a store running on the Docker host with a private CA (see below) |

Generate a token with `python -c "import secrets; print(secrets.token_urlsafe(24))"`.

## How the containers are arranged

```
 browser / MCP client
        |  :8080 (only published port)
        v
   web (nginx) ---- edge network ---- gateway ---- default network ---- postgres
                                                     |
                                                     +---- woocommerce-mcp ---> your store
                                                     +---- utilities-mcp
                                                     +---- docs-mcp
```

- **Two networks.** `edge` holds the dashboard and the gateway. `default` holds the gateway, PostgreSQL
  and the MCP servers. The dashboard therefore cannot reach the database or any MCP server, and the
  MCP servers can only be called by the gateway. This is checked, not assumed: a request from the
  dashboard container to an MCP server fails with "bad address".
- **Production network policy.** The gateway runs with `UPSTREAM_ALLOW_PRIVATE_NETWORKS=false` and
  `UPSTREAM_ALLOWED_HOSTS` listing exactly the three servers, so an admin cannot register a URL that
  points at the database, the metadata service, or anything else internal.
- **Hardening.** Every container runs as a non-root user, with a read-only filesystem, all Linux
  capabilities dropped and `no-new-privileges`. nginx keeps only its generated config and `/tmp`
  writable, in memory.
- **nginx** serves the dashboard with a strict Content-Security-Policy (nothing is loaded from another
  origin), forwards `/api`, `/mcp` and `/lite` to the gateway without buffering, returns 404 for `/metrics`,
  caches hashed assets for a year and never caches `index.html`.
- **Images** contain no `.env` files and no secrets; each build ignores them via `.dockerignore`.
  Sizes: gateway 312 MB, MCP servers about 240 MB, dashboard 74 MB.
- **Migrations** run when the gateway starts (`RUN_MIGRATIONS=1`). With several gateway replicas, run
  them as a single job instead and leave the variable unset on the replicas.

## A store on your own machine (Local by Flywheel)

A container cannot see the host's `mcp-store.local`. Point its name at the host and trust the store's
certificate:

```powershell
$env:STORE_HOSTNAME = "mcp-store.local"
$env:WOOCOMMERCE_BASE_URL = "https://mcp-store.local"
$env:WOOCOMMERCE_CA_BUNDLE = "/certs/store.crt"
```

and add a small override file that mounts the certificate (Local keeps it under
`%APPDATA%\Local\run\router\nginx\certs\`):

```yaml
# compose.local-store.yaml
services:
  woocommerce-mcp:
    volumes:
      - C:/Users/<you>/AppData/Roaming/Local/run/router/nginx/certs/mcp-store.local.crt:/certs/store.crt:ro
```

```powershell
docker compose -f compose.yaml -f compose.full.yaml -f compose.local-store.yaml up -d --build
```

## Verifying a running stack

```powershell
cd frontend
$env:STACK_URL = "http://127.0.0.1:8080"
$env:STACK_ADMIN_EMAIL = "<your bootstrap admin>"
$env:STACK_ADMIN_PASSWORD = "<its password>"
npm run test:e2e:stack      # real browser, through nginx: CSP, deep links, key -> MCP call -> audit log
```

## Stopping and cleaning up

```powershell
docker compose -f compose.yaml -f compose.full.yaml down        # stop, keep the database
docker compose -f compose.yaml -f compose.full.yaml down -v     # also delete the database volume
```
