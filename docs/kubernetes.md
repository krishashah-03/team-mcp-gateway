# Running on Kubernetes (Helm)

The chart is in `deploy/helm/team-mcp-gateway/`. It deploys the gateway, dashboard (nginx), the three
MCP servers and PostgreSQL, wired exactly like `compose.full.yaml`.

```
Ingress / port-forward -> web (nginx) -> gateway -> postgres
                                            |-> woocommerce-mcp -> your store
                                            |-> utilities-mcp
                                            |-> docs-mcp
```

## Prerequisites

| Tool | Why | Install (Windows) |
|---|---|---|
| A cluster | k3s (Linux/WSL2), or Docker Desktop > Settings > Kubernetes > Enable | `curl -sfL https://get.k3s.io \| sh -` inside WSL2 |
| `kubectl` | talk to the cluster | included with Docker Desktop |
| `helm` 3.x | install the chart | `winget install Helm.Helm` |
| Docker | build the images | Docker Desktop |

## 1. Build the images and make the cluster see them

```powershell
docker build -t team-mcp-gateway/gateway:local ./backend
docker build -t team-mcp-gateway/web:local ./frontend
docker build -t team-mcp-gateway/woocommerce-mcp:local ./mcp-servers/woocommerce
docker build -t team-mcp-gateway/utilities-mcp:local ./mcp-servers/utilities
docker build -t team-mcp-gateway/docs-mcp:local ./mcp-servers/docs-search
```

- **Docker Desktop Kubernetes (kubeadm mode):** nothing more; it shares Docker's images.
- **k3s:** `docker save <image> | sudo k3s ctr images import -` for each image.
- **kind:** `kind load docker-image <image>`.
- **A real cluster:** push to a registry and set `image.registry` and `image.tag`.

## 2. Create the Secret

The chart never contains secrets; it reads one Secret. Either:

```powershell
.\deploy\scripts\create-secret.ps1 -AdminEmail you@example.com -AdminPassword "choose-10+-chars" `
    -WooKey ck_xxx -WooSecret cs_xxx [-GeminiKey AIza...]
```

(generates the random values itself and writes nothing to disk), or copy
`deploy/secrets.env.example` to `deploy/secrets.env`, fill it in and run
`kubectl create secret generic team-mcp-gateway-secrets --from-env-file=deploy/secrets.env`.
`deploy/secrets.env` is git-ignored.

## 3. Install

```powershell
helm install tmg deploy/helm/team-mcp-gateway `
  -f deploy/helm/team-mcp-gateway/values-local.yaml `
  --set woocommerce.baseUrl=https://your-store.example.com

kubectl get pods -w
kubectl port-forward svc/tmg-team-mcp-gateway-web 8080:8080     # http://localhost:8080
```

Upgrade: `helm upgrade tmg deploy/helm/team-mcp-gateway -f ... `. Remove: `helm uninstall tmg`
(the database volume stays; delete it with `kubectl delete pvc -l app.kubernetes.io/instance=tmg`).

### A store on your own machine (Local by Flywheel)

```powershell
kubectl create secret generic woocommerce-ca --from-file=ca.crt=C:\path\to\mcp-store.local.crt
helm upgrade --install tmg deploy/helm/team-mcp-gateway -f deploy/helm/team-mcp-gateway/values-local.yaml `
  --set woocommerce.baseUrl=https://mcp-store.local `
  --set woocommerce.caBundleSecret=woocommerce-ca `
  --set "woocommerce.hostAliases[0].ip=<host IP reachable from pods>" `
  --set "woocommerce.hostAliases[0].hostnames[0]=mcp-store.local"
```

## Production values

```yaml
image: {registry: ghcr.io/your-org, tag: "1.0.0", pullPolicy: IfNotPresent}
publicHosts: "gateway.example.com"
ingress: {enabled: true, className: nginx, host: gateway.example.com, tls: {enabled: true, secretName: gateway-tls},
          annotations: {nginx.ingress.kubernetes.io/proxy-buffering: "off", nginx.ingress.kubernetes.io/proxy-read-timeout: "300"}}
postgres: {enabled: false}      # managed database; put DATABASE_URL in the Secret
gateway: {migrations: hook}     # one migration Job per upgrade instead of an init container
```

## Design notes

- **Network isolation:** a default-deny ingress policy, then only: anyone -> web:8080, web -> gateway:8000,
  gateway -> MCP servers, gateway -> postgres. Verified on k3s: the dashboard pod cannot reach an MCP
  server or the database, and can again once the policies are removed. Docker Desktop's built-in
  cluster does not enforce NetworkPolicy; k3s does.
- **Upstream policy:** `UPSTREAM_ALLOW_PRIVATE_NETWORKS=false` with the three Service names allow-listed,
  so an admin cannot register an internal URL.
- **Hardening:** non-root, read-only root filesystem, all capabilities dropped, seccomp RuntimeDefault, no
  service-account token mounted. PostgreSQL runs as uid 999 with capabilities dropped, but its root
  filesystem is writable (the image needs that).
- **Migrations:** `gateway.migrations: init` (default) runs `alembic upgrade head` in an init container;
  fine for one replica. With several replicas use `hook`.
- **Replicas:** rate limits and the tool cache are per process, so keep `gateway.replicas: 1` until a shared
  store (Redis) is added. See [system-design.md](system-design.md).
- **Not included:** egress policies, autoscaling, backups of the database volume, Prometheus ServiceMonitor.
