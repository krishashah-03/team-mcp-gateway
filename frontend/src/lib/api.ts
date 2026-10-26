// Typed client for the gateway's admin API. Same-origin: the dev server proxies /api,
// and in production the web server in front of the dashboard does.

export type Role = "admin" | "member";
export type AuthType = "none" | "bearer" | "header";
export type ServerStatus = "connected" | "unreachable" | "disabled";

export interface User {
  id: number;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string | null;
}

export interface LoginResult {
  access_token: string;
  expires_in: number;
  user: User;
}

export interface ServerTool {
  name: string;
  namespaced_name: string;
  description: string | null;
}

export interface McpServer {
  id: number;
  name: string;
  slug: string;
  endpoint_url: string;
  description: string | null;
  is_enabled: boolean;
  auth_type: AuthType;
  auth_header_name: string | null;
  has_credential: boolean;
  status: ServerStatus;
  status_detail: string | null;
  checked_at: string | null;
  tools: ServerTool[];
}

export interface ServerInput {
  name: string;
  slug: string;
  endpoint_url: string;
  description?: string | null;
  is_enabled?: boolean;
  auth_type: AuthType;
  auth_header_name?: string | null;
  credential?: string | null;
}

export type ServerPatch = Partial<Omit<ServerInput, "slug">>;

export interface ApiKey {
  id: number;
  key_id: string;
  name: string;
  user_id: number;
  user_email: string | null;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface IssuedApiKey extends ApiKey {
  key: string;
}

export interface GatewayTool {
  name: string;
  description: string | null;
  input_schema: Record<string, unknown>;
}

export interface AuditEvent {
  id: number;
  occurred_at: string | null;
  actor_user_id: number | null;
  actor_email: string | null;
  channel: string;
  server: string | null;
  tool: string | null;
  operation: string;
  duration_ms: number;
  outcome: "success" | "failure";
  error_category: string | null;
}

export interface AuditPage {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface PermissionRule {
  id: number;
  role: Role | null;
  user_id: number | null;
  server_slug: string;
  tool_name: string | null;
  effect: "allow" | "deny";
  created_at: string | null;
}

export interface ToolCallTrace {
  name: string;
  arguments: Record<string, unknown>;
  is_error: boolean;
  duration_ms: number;
  result_preview: string;
}

export interface ChatResponse {
  conversation_id: number;
  reply: string;
  tool_calls: ToolCallTrace[];
  steps: number;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  tool_calls: ToolCallTrace[] | null;
  created_at: string | null;
}

/** An API failure with a message that is safe to show to the user. */
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const TOKEN_KEY = "mcp-gateway.token";
let unauthorizedHandler: (() => void) | null = null;

// sessionStorage keeps the token only for this tab; closing it signs the user out.
export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: the session simply will not survive a reload */
    }
  },
  clear(): void {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** FastAPI errors are either {detail: "text"} or {detail: [{loc, msg}, ...]}. */
export function describeError(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
      const field = Array.isArray(first?.loc) ? String(first.loc[first.loc.length - 1]) : "";
      return first?.msg ? (field ? `${field}: ${first.msg}` : first.msg) : "The request was not valid";
    }
  }
  if (status === 413) return "That request is too large.";
  if (status === 429) return "Too many requests. Please wait a moment.";
  if (status >= 500) return "The server had a problem. Try again shortly.";
  return `Request failed (${status})`;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  { auth = true }: { auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = tokenStore.get();
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Cannot reach the gateway. Check that it is running.", 0);
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body */
  }

  if (!response.ok) {
    // A 401 on a signed-in request means the session expired or was revoked.
    if (response.status === 401 && auth && token) unauthorizedHandler?.();
    const retry = Number(response.headers.get("Retry-After"));
    throw new ApiError(
      describeError(payload, response.status),
      response.status,
      Number.isFinite(retry) && retry > 0 ? retry : undefined,
    );
  }
  return payload as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown, opts?: { auth?: boolean }) =>
  request<T>("POST", path, body ?? {}, opts);
const patch = <T>(path: string, body: unknown) => request<T>("PATCH", path, body);
const del = <T>(path: string) => request<T>("DELETE", path);

export const api = {
  login: (email: string, password: string) =>
    post<LoginResult>("/auth/login", { email, password }, { auth: false }),
  me: () => get<User>("/auth/me"),

  servers: (refresh = false) => get<McpServer[]>(refresh ? "/servers?refresh=true" : "/servers"),
  createServer: (input: ServerInput) => post<McpServer>("/servers", input),
  updateServer: (id: number, input: ServerPatch) => patch<McpServer>(`/servers/${id}`, input),
  deleteServer: (id: number) => del<void>(`/servers/${id}`),
  refreshServer: (id: number) => post<McpServer>(`/servers/${id}/refresh`),

  users: () => get<User[]>("/users"),
  createUser: (email: string, password: string, role: Role) =>
    post<User>("/users", { email, password, role }),
  updateUser: (id: number, input: { role?: Role; is_active?: boolean; password?: string }) =>
    patch<User>(`/users/${id}`, input),

  myKeys: () => get<ApiKey[]>("/keys"),
  allKeys: () => get<ApiKey[]>("/admin/keys"),
  createKey: (name: string, expiresInDays?: number) =>
    post<IssuedApiKey>("/keys", { name, expires_in_days: expiresInDays ?? null }),
  createKeyFor: (userId: number, name: string, expiresInDays?: number) =>
    post<IssuedApiKey>(`/users/${userId}/keys`, { name, expires_in_days: expiresInDays ?? null }),
  revokeKey: (keyId: string) => del<ApiKey>(`/keys/${keyId}`),

  tools: () => get<GatewayTool[]>("/tools"),

  permissions: () => get<PermissionRule[]>("/permissions"),
  setPermission: (rule: {
    role?: Role;
    user_id?: number;
    server_slug: string;
    tool_name?: string | null;
    effect: "allow" | "deny";
  }) => post<PermissionRule>("/permissions", rule),
  deletePermission: (id: number) => del<void>(`/permissions/${id}`),

  audit: (params: { user_id?: number; server?: string; outcome?: string; limit: number; offset: number }) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return get<AuditPage>(`/audit?${query}`);
  },

  chat: (message: string, conversationId?: number) =>
    post<ChatResponse>("/chat", { message, conversation_id: conversationId ?? null }),
  conversations: () => get<Conversation[]>("/chat/conversations"),
  conversation: (id: number) => get<ChatMessage[]>(`/chat/conversations/${id}`),
  deleteConversation: (id: number) => del<void>(`/chat/conversations/${id}`),
};

/** Turn a display name into a valid server slug. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    // Drop the combining accent left behind by decomposition (u + diaeresis), so
    // "Münü" becomes "munu" and not "mu-nu".
    .replace(/\p{M}/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
}
