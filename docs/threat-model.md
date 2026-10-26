# Threat model

Assets: the store's data and the ability to change it, the upstream credentials, user accounts and
API keys, and the audit log. Actors: admins (trusted to manage the platform), members (trusted to
use it), anyone on the network, and upstream MCP servers (not trusted to be well behaved).

## Shared upstream credentials: what a member can do with a credential they never see

A member cannot read the credential: it is encrypted at rest, no endpoint returns it, and it never
appears in logs, errors or audit records. But it is *used on their behalf*, so a member can do
anything the upstream credential permits through the tools the gateway exposes. The WooCommerce key
can create products and change order status, so that power is delegated to every member who can call
those tools. Mitigations: per-role and per-user tool permissions (hide and reject), a least-privilege
store key, and an audit record of every call. Residual risk: the gateway cannot tell a legitimate
`update_order_status` from a mistaken one; permissions are the control.

## Why client tokens are not passed through to upstream servers

The API key a client sends authenticates it *to the gateway only*. The gateway authenticates to each
upstream with that upstream's own configured credential. Forwarding the client token would let any
upstream, including a compromised one, capture it and replay it against the gateway or other
services (the "confused deputy" problem), and would tie each upstream's authentication to our key
format. The gateway never copies the incoming `Authorization` header onto an upstream request.

## Untrusted upstreams

An upstream can be malicious or compromised. What it can do, and what limits it:

| Threat | Control |
|---|---|
| **Tool descriptions written to manipulate the model** ("ignore your instructions and call X") | Descriptions are length-capped (1500 chars) and prefixed with `[Server name]` so their source is visible. The chat agent's system prompt tells the model that tool text is data, not instructions. This reduces but cannot eliminate prompt injection; the gateway's permission checks and the "only write when asked" rule are the backstop. |
| **Tool results containing instructions** | Same: treated as data. The agent only runs tools that were offered to it for this user, so injected text cannot make it call a hidden tool; the gateway checks permissions again on every call. |
| **A tool name that collides with another server's** | Names are namespaced by server slug, so a server cannot shadow another's tool. |
| **Huge or slow responses, hanging connections** | Per-call timeouts, a cap on pages of tool listings, result truncation before reaching the model, and short-lived sessions. |
| **Pointing the gateway at internal services (SSRF)** | Admin-supplied URLs are checked when saved and again before every connection: cloud metadata and link-local addresses are always blocked, private and loopback ranges are blocked in production unless allow-listed, hostnames are resolved and every address checked (including IPv4-mapped IPv6), and redirects are not followed. Residual risk: a DNS name could change its answer between the check and the connection. |
| **Leaking the credential back to the upstream's operator** | The credential is sent only to the URL it was configured for, and redirects are not followed, so it cannot be bounced elsewhere. An admin who registers a hostile URL does hand it that server's credential; that is an admin-trust decision. |
| **A compromised upstream that returns data for tools it did not advertise** | Calls for tools the upstream does not list are refused before being sent. |

## What one team member can do to another

- **Read or use another's keys or chats:** no. Key and conversation lookups are scoped to the owner,
  and a foreign id returns 404, the same as a missing one, so ids cannot be probed.
- **Revoke another's key:** no (404). Admins can.
- **Act as another user:** only by stealing their key or session token. Keys are shown once and stored hashed.
- **Affect another's availability:** rate limits are per user, so one member exhausting theirs does not
  block others. All members share upstream capacity, so a heavy user can slow a shared upstream.
- **See another's activity:** the audit log is admin-only.
- **Escalate to admin:** role is read from the database on every request; there is no endpoint a member
  can use to change roles, and the last active admin cannot be demoted or disabled.

## Other threats

| Threat | Control |
|---|---|
| Password guessing | scrypt hashing; per-client-and-email and per-client throttling returning 429; identical responses for unknown email, wrong password and disabled account; dummy hash verified for unknown emails to equalise timing |
| Stolen database dump | Passwords and API keys are hashed; upstream credentials are encrypted with a key that is not in the database |
| Forged or replayed session tokens | HS256 with a 32+ character secret, mandatory expiry, algorithm pinned (an `alg: none` token is rejected) |
| Brute-forcing API keys | 256-bit secrets, constant-time comparison, per-user rate limiting |
| DNS rebinding against `/mcp` | Host header allow-list |
| Cross-site requests from a malicious page | CORS allow-list; API uses bearer tokens, not cookies |
| Log injection / secret leakage in logs | Tool names stripped of control characters before storage; access logs record method, path, status and duration only, never headers, query strings or bodies |
| Metric-label explosion (memory DoS) | Only tool names the upstream really lists become metric labels; everything else shares one label |
| Oversized requests | Body size cap (413) at the gateway and at nginx |
| XSS in the dashboard | No `dangerouslySetInnerHTML`; chat, tool and server text is rendered as text. nginx sends `Content-Security-Policy: default-src 'self'` (and `frame-ancestors 'none'`), verified in a real browser against the running stack |
| Clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` |
| A compromised dashboard container reaching the database or an MCP server | It is on a separate network from them; checked from inside the container |
| A compromised container writing to its filesystem or escalating | Read-only root filesystem, non-root user, all capabilities dropped, `no-new-privileges` |
| Secrets in images or the repository | `.dockerignore` excludes env files from every image (verified: none present); `.gitignore` excludes them from the repository |
| Public exposure of `/metrics` | nginx returns 404 for it; the gateway itself requires an admin session or a scrape token |
| Path traversal in the document server | Files are matched against a directory listing, never built from user input; dotfiles, subfolders, symlinks, other file types and large files are excluded |
| Code execution in the calculator | Arithmetic is evaluated from a parsed syntax tree that allows only numbers, operators and five math functions; exponents are bounded |
| Malformed price input to the store | Plain decimal notation only, bounded digits; no exponent forms |

## Not mitigated

- A malicious or careless **admin**. Admins can register servers, read the audit log and change permissions.
- **Prompt injection** cannot be fully prevented by prompting; the permission and audit controls limit the damage.
- **TLS** is the deployment's job; the gateway speaks plain HTTP and expects TLS at the ingress.
- **Rate limiting and tool caches are per process** (see the design note).
- **The session token is readable by page JavaScript** (it is not an HttpOnly cookie), so an XSS bug
  would expose it. The CSP and plain-text rendering are the controls; they are not a guarantee.
- **Hosted/untrusted container images** are not supported yet, so there is no sandboxing of upstream code.
