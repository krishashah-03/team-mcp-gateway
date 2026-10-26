# Architecture diagram for the demo

![Team MCP Gateway architecture diagram](architecture-demo.svg)

[Open the standalone SVG](architecture-demo.svg)

This page shows the current source-level design for the team MCP gateway. It describes the
request paths and Helm resources present in the repository; it does not claim that a Kubernetes
release is running or has been verified.

## Application and request flow

```mermaid
flowchart LR
  Admin["Admin<br/>dashboard session"]
  Member["Member<br/>dashboard session"]
  McpClient["External MCP client<br/>gateway API key"]

  subgraph Browser["React dashboard"]
    AdminUI["Servers, team,<br/>permissions"]
    ToolsUI["Tools page<br/>direct Run tool"]
    ChatUI["Chat page"]
  end

  subgraph Gateway["FastAPI gateway"]
    API["REST API<br/>/api/v1/*"]
    ToolAPI["Direct tool call<br/>/tools/call"]
    ChatAPI["Chat endpoint<br/>/chat"]
    MCP["MCP transports<br/>/mcp and /lite/mcp"]
    Identity["Authenticate<br/>JWT or API key"]
    ChatAgent["Chat agent<br/>model tool loop"]
    Permission["Authorize as caller<br/>role and tool rules"]
    Router["Discover and route<br/>namespaced tools"]
    Audit["Audit outcome<br/>actor, tool, duration"]
  end

  subgraph ModelProviders["Optional model provider"]
    Model["Gemini or OpenRouter"]
  end

  subgraph Upstreams["Internal upstream MCP servers"]
    WooMCP["WooCommerce MCP"]
    Utilities["Utilities MCP"]
    Docs["Docs-search MCP"]
  end

  WooREST["WooCommerce REST API<br/>products and orders"]
  PG[("PostgreSQL<br/>gateway-managed data only")]

  Admin -->|signs in| AdminUI
  Member -->|signs in| ToolsUI
  Member -->|signs in| ChatUI
  AdminUI -->|JWT| API
  ToolsUI -->|JWT| ToolAPI
  ChatUI -->|JWT| ChatAPI
  McpClient -->|Bearer gateway key| MCP

  API --> Identity
  ToolAPI --> Identity
  ChatAPI --> Identity
  MCP --> Identity
  Identity --> Permission
  ChatAPI --> ChatAgent
  ChatAgent <-->|prompt and tool choice| Model
  ChatAgent -->|tool call as logged-in user| Permission
  Permission --> Router
  Router -->|MCP over Streamable HTTP| WooMCP
  Router -->|MCP over Streamable HTTP| Utilities
  Router -->|MCP over Streamable HTTP| Docs
  WooMCP -->|WooCommerce credentials stay here| WooREST

  Identity -->|user and key metadata| PG
  API -->|servers, permissions, conversations| PG
  Permission -->|audit event| Audit
  Audit --> PG

  classDef actor fill:#eef2ff,stroke:#5965d8,color:#172554;
  classDef gateway fill:#e9f4ff,stroke:#3485b8,color:#102a43;
  classDef upstream fill:#effaf3,stroke:#4b9b68,color:#173b24;
  class Admin,Member,McpClient actor;
  class API,MCP,Identity,ChatAgent,Permission,Router,Audit gateway;
  class WooMCP,Utilities,Docs,WooREST upstream;
```

### Reading the flow

- The dashboard's **Tools → Run tool** path calls the gateway directly. It does not call Gemini or
  OpenRouter, but still uses the signed-in user's identity, permission checks, and audit path.
- The Chat path first asks the configured model to choose a tool. The gateway executes that tool as
  the signed-in user, returns the result to the model, then returns the assistant response.
- External MCP clients use a gateway API key and `/mcp` or `/lite/mcp`. Tool names include the
  server slug, such as `woocommerce__list_products`.
- The gateway stores users, hashed gateway keys, server registrations, permission rules, audit events,
  and chat history. Product and order records stay in WooCommerce.
- The gateway-to-MCP credential is distinct from the WooCommerce Consumer Key/Secret. The latter are
  used by the WooCommerce MCP server when it calls the WooCommerce REST API.

## Kubernetes deployment shape

```mermaid
flowchart TB
  Browser["Team member or admin browser"]
  MCPClient["External MCP client"]

  subgraph Cluster["Kubernetes cluster"]
    subgraph ReleaseNS["Helm release namespace<br/>selected at install time"]
      Ingress["Optional Ingress<br/>host and TLS Secret reference"]
      Web["web Deployment + Service<br/>nginx :8080"]
      Gateway["gateway Deployment + Service<br/>FastAPI :8000"]
      Migration["Migration Job"]
      Woo["WooCommerce MCP<br/>Deployment + Service :8001"]
      Util["Utilities MCP<br/>Deployment + Service :8003"]
      DocSearch["Docs-search MCP<br/>Deployment + Service :8004"]
      DB["PostgreSQL StatefulSet + Service<br/>:5432"]
      PVC[("PersistentVolumeClaim<br/>PostgreSQL data")]
      K8sSecrets["Kubernetes Secret references<br/>values intentionally omitted"]
      NP["NetworkPolicies<br/>default-deny ingress + explicit paths"]
    end
  end

  WooStore["WooCommerce REST API"]
  LLM["Gemini / OpenRouter API<br/>optional"]

  Browser -->|HTTPS when TLS is enabled| Ingress
  MCPClient -->|gateway endpoint via web proxy| Ingress
  Ingress -->|only public application entry| Web
  Web -->|/api, /mcp, /lite| Gateway
  Gateway -->|internal MCP calls| Woo
  Gateway -->|internal MCP calls| Util
  Gateway -->|internal MCP calls| DocSearch
  Gateway --> DB
  Migration -. schema upgrade .-> DB
  DB --- PVC
  K8sSecrets -. gateway and shared MCP credentials .-> Gateway
  K8sSecrets -. Woo and shared MCP credentials .-> Woo
  K8sSecrets -. database credentials .-> DB
  K8sSecrets -. TLS reference .-> Ingress
  Woo -. outbound HTTPS .-> WooStore
  Gateway -. optional model requests .-> LLM
  NP -. constrains incoming pod traffic .-> Web
  NP -. constrains incoming pod traffic .-> Gateway
  NP -. constrains incoming pod traffic .-> Woo
  NP -. constrains incoming pod traffic .-> DB
```

### Kubernetes notes for the presenter

- The chart templates do not create a Namespace object. Install the Helm release into the namespace
  chosen for that environment.
- Ingress is optional and routes to the web service. The web service proxies dashboard API and MCP
  paths to the gateway. The gateway, MCP services, and PostgreSQL have internal Services.
- PostgreSQL uses a StatefulSet and a PVC. The chart also includes a migration Job.
- The templates define readiness/liveness probes and resource request/limit settings. Use the
  selected Helm values to show their actual configured values; do not guess those numbers.
- NetworkPolicies in the chart restrict **ingress** paths: traffic to web, web to gateway, gateway
  to MCP servers, and gateway to PostgreSQL. The reviewed policies do not restrict egress; do not
  claim that they prevent outbound connections from a compromised pod.
- Secret values are supplied through Kubernetes Secret references. Show the references or secret
  names only, never decoded values. Compare local and VPS Helm values with credential fields hidden.
- The Helm chart files establish intended resources, not proof that a cluster deployment succeeded.

## Chat quota and demo fallback

The direct Tools page is the reliable live path for product operations when the model provider is
throttled. In the current gateway code, no configured provider produces `503`; the gateway's own
per-user chat limiter produces `429`; provider `429` or `5xx` failures are retried once and then
returned through the chat route as `502`.
