import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { AuthProvider } from "./auth";
import { tokenStore } from "./lib/api";
import { admin, member } from "./test-utils";

// The real API client runs here; only the network is stubbed.
const fetchMock = vi.fn();
type Route = (init: RequestInit) => Response | Promise<Response>;
let routes: Record<string, Route> = {};

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function mountAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function signIn(email: string, password = "a-long-password-1") {
  await userEvent.type(await screen.findByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  sessionStorage.clear();
  routes = {
    "POST /api/v1/auth/login": async (init) => {
      const { email, password } = JSON.parse(String(init.body));
      if (password !== "a-long-password-1") return json(401, { detail: "Invalid email or password" });
      const user = email === admin.email ? admin : member;
      return json(200, { access_token: `token-for-${user.id}`, expires_in: 3600, user });
    },
    "GET /api/v1/auth/me": () => json(200, member),
    "GET /api/v1/chat/conversations": () => json(200, []),
    "GET /api/v1/servers": () => json(200, []),
    "GET /api/v1/tools": () => json(200, []),
    "GET /api/v1/keys": () => json(200, []),
  };
  fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
    const route = routes[`${init.method ?? "GET"} ${url}`];
    if (!route) throw new Error(`Unexpected request: ${init.method ?? "GET"} ${url}`);
    return route(init);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("signing in", () => {
  it("shows the login form to anonymous visitors, whatever the URL", async () => {
    mountAt("/audit");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("lands a member on chat with no admin navigation", async () => {
    mountAt("/");
    await signIn(member.email);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Chat" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "My API keys" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "MCP servers" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
    expect(await screen.findByText("Ask about your store")).toBeInTheDocument();
    expect(tokenStore.get()).toBe("token-for-2");
  });

  it("lands an admin on the servers page with the admin navigation", async () => {
    mountAt("/");
    await signIn(admin.email);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const name of ["MCP servers", "Team", "Permissions", "Audit log"]) {
      expect(within(nav).getByRole("link", { name })).toBeInTheDocument();
    }
    expect(await screen.findByRole("heading", { name: "MCP servers" })).toBeInTheDocument();
  });

  it("shows the server's message for a wrong password and stays signed out", async () => {
    mountAt("/");
    await signIn(member.email, "wrong-password-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(tokenStore.get()).toBeNull();
  });

  it("tells the user when they are being throttled", async () => {
    routes["POST /api/v1/auth/login"] = () => json(429, { detail: "Too many failed sign-in attempts. Try again later." }, { "Retry-After": "600" });
    mountAt("/");
    await signIn(member.email);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Too many failed attempts\. Try again in about 10 min/);
  });

  it("explains an unreachable gateway", async () => {
    routes["POST /api/v1/auth/login"] = () => {
      throw new TypeError("Failed to fetch");
    };
    mountAt("/");
    await signIn(member.email);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the gateway");
  });
});

describe("role-based access", () => {
  it("blocks a member from an admin page by URL", async () => {
    tokenStore.set("token-for-2");
    mountAt("/servers");
    expect(await screen.findByText("Admins only")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/v1/servers")).toBe(false); // never even requested
  });

  it.each(["/team", "/permissions", "/audit"])("blocks a member from %s", async (path) => {
    tokenStore.set("token-for-2");
    mountAt(path);
    expect(await screen.findByText("Admins only")).toBeInTheDocument();
  });

  it("lets an admin in", async () => {
    tokenStore.set("token-for-1");
    routes["GET /api/v1/auth/me"] = () => json(200, admin);
    mountAt("/servers");
    expect(await screen.findByRole("heading", { name: "MCP servers" })).toBeInTheDocument();
  });

  it("shows a not-found page for unknown routes", async () => {
    tokenStore.set("token-for-2");
    mountAt("/nope");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});

describe("sessions", () => {
  it("restores a session after a reload", async () => {
    tokenStore.set("token-for-2");
    mountAt("/chat");
    expect(await screen.findByText("Ask about your store")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0]![1].headers.Authorization).toBe("Bearer token-for-2");
  });

  it("drops a stored token the server no longer accepts", async () => {
    tokenStore.set("revoked");
    routes["GET /api/v1/auth/me"] = () => json(401, { detail: "Not authenticated" });
    mountAt("/chat");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(tokenStore.get()).toBeNull();
  });

  it("returns to the login page with a notice when the session ends mid-use", async () => {
    mountAt("/");
    await signIn(member.email);
    await screen.findByText("Ask about your store");
    routes["GET /api/v1/tools"] = () => json(401, { detail: "Not authenticated" });
    await userEvent.click(screen.getByRole("link", { name: "Tools" }));
    expect(await screen.findByText("Your session ended. Please sign in again.")).toBeInTheDocument();
    expect(tokenStore.get()).toBeNull();
  });

  it("signs out", async () => {
    mountAt("/");
    await signIn(member.email);
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument());
    expect(tokenStore.get()).toBeNull();
  });
});
