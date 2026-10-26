import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, describeError, onUnauthorized, slugify, tokenStore } from "./api";

function respond(status: number, body?: unknown, headers: Record<string, string> = {}) {
  return Promise.resolve(
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    }),
  );
}

describe("describeError", () => {
  it("uses a string detail", () => {
    expect(describeError({ detail: "Invalid email or password" }, 401)).toBe("Invalid email or password");
  });

  it("names the field for validation errors", () => {
    expect(describeError({ detail: [{ loc: ["body", "email"], msg: "value is not a valid email" }] }, 422)).toBe(
      "email: value is not a valid email",
    );
  });

  it("falls back to a friendly message by status", () => {
    expect(describeError(null, 413)).toMatch(/too large/);
    expect(describeError(null, 429)).toMatch(/Too many/);
    expect(describeError(null, 502)).toMatch(/server had a problem/);
    expect(describeError({}, 418)).toBe("Request failed (418)");
  });

  it("never echoes an unexpected body shape", () => {
    expect(describeError({ detail: { secret: "x" } }, 400)).toBe("Request failed (400)");
    expect(describeError([{ weird: true }], 422)).toBe("Request failed (422)");
  });
});

describe("slugify", () => {
  it.each([
    ["WooCommerce", "woocommerce"],
    ["My Test Server!", "my-test-server"],
    ["  --Docs  Search--  ", "docs-search"],
    ["Café Münü", "cafe-munu"],
    ["a".repeat(60), "a".repeat(40)],
    ["!!!", ""],
  ])("%s -> %s", (input, expected) => {
    expect(slugify(input)).toBe(expected);
  });

  it("never ends with a hyphen after truncation", () => {
    expect(slugify(`${"a".repeat(39)} b`)).toBe("a".repeat(39));
  });
});

describe("request handling", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    sessionStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => {
    onUnauthorized(null);
    vi.unstubAllGlobals();
  });

  it("sends the session token as a bearer header", async () => {
    tokenStore.set("tok123");
    fetchMock.mockReturnValue(respond(200, []));
    await api.servers();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/servers");
    expect(init.headers.Authorization).toBe("Bearer tok123");
  });

  it("does not send a token when logging in", async () => {
    tokenStore.set("stale");
    fetchMock.mockReturnValue(respond(200, { access_token: "t", expires_in: 1, user: {} }));
    await api.login("a@b.test", "password-123");
    expect(fetchMock.mock.calls[0]![1].headers.Authorization).toBeUndefined();
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ email: "a@b.test", password: "password-123" });
  });

  it("returns undefined for 204 responses", async () => {
    tokenStore.set("t");
    fetchMock.mockReturnValue(respond(204));
    await expect(api.deleteServer(3)).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0]![1].method).toBe("DELETE");
  });

  it("signals an expired session on 401 only when signed in", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    tokenStore.set("t");
    fetchMock.mockReturnValue(respond(401, { detail: "Not authenticated" }));
    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledTimes(1);

    handler.mockClear();
    tokenStore.clear();
    fetchMock.mockReturnValue(respond(401, { detail: "Invalid email or password" }));
    await expect(api.login("a@b.test", "x")).rejects.toThrow("Invalid email or password");
    expect(handler).not.toHaveBeenCalled(); // a failed login is not an expired session
  });

  it("exposes Retry-After on throttled requests", async () => {
    fetchMock.mockReturnValue(respond(429, { detail: "Too many failed sign-in attempts." }, { "Retry-After": "120" }));
    await expect(api.login("a@b.test", "x")).rejects.toMatchObject({ status: 429, retryAfter: 120 });
  });

  it("turns a network failure into a clear message", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await api.login("a@b.test", "x").catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, message: expect.stringMatching(/Cannot reach the gateway/) });
  });

  it("copes with a non-JSON error body", async () => {
    fetchMock.mockReturnValue(Promise.resolve(new Response("<html>Bad gateway</html>", { status: 502 })));
    await expect(api.servers()).rejects.toThrow(/server had a problem/);
  });

  it("builds the audit query without empty filters", async () => {
    tokenStore.set("t");
    fetchMock.mockReturnValue(respond(200, { items: [], total: 0, limit: 25, offset: 0 }));
    await api.audit({ user_id: undefined, server: "", outcome: "failure", limit: 25, offset: 50 });
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/audit?outcome=failure&limit=25&offset=50");
  });

  it("sends a new conversation without an id", async () => {
    tokenStore.set("t");
    fetchMock.mockReturnValue(respond(200, { conversation_id: 1, reply: "", tool_calls: [], steps: 1 }));
    await api.chat("hello");
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ message: "hello", conversation_id: null });
  });
});

describe("token store", () => {
  it("survives unavailable storage", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(tokenStore.get()).toBeNull();
    spy.mockRestore();
  });
});
