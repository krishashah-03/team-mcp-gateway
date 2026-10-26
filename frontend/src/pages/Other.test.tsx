import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ApiKey } from "../lib/api";
import { admin, makeServer, member, renderPage } from "../test-utils";
import AuditPage from "./AuditPage";
import ChatPage from "./ChatPage";
import KeysPage, { keyStatus } from "./KeysPage";
import PermissionsPage, { describeRule } from "./PermissionsPage";
import TeamPage from "./TeamPage";
import ToolsPage, { groupByServer } from "./ToolsPage";

const apiMock = vi.hoisted(() => ({
  myKeys: vi.fn(),
  allKeys: vi.fn(),
  createKey: vi.fn(),
  createKeyFor: vi.fn(),
  revokeKey: vi.fn(),
  users: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  servers: vi.fn(),
  tools: vi.fn(),
  permissions: vi.fn(),
  setPermission: vi.fn(),
  deletePermission: vi.fn(),
  audit: vi.fn(),
  chat: vi.fn(),
  conversations: vi.fn(),
  conversation: vi.fn(),
  deleteConversation: vi.fn(),
}));

vi.mock("../lib/api", async (original) => ({ ...(await original<typeof import("../lib/api")>()), api: apiMock }));
vi.mock("../auth", () => ({ useAuth: () => ({ user: admin }) }));

const NEW_KEY = "gwk_0123456789abcdef0123456789abcdef_SECRETSECRETSECRETSECRETSECRETSECRETSECRE";

function key(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: 1,
    key_id: "0123456789abcdef0123456789abcdef",
    name: "laptop",
    user_id: 2,
    user_email: "alice@example.com",
    created_at: "2026-10-20T00:00:00Z",
    expires_at: null,
    revoked_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  apiMock.myKeys.mockResolvedValue([key()]);
  apiMock.allKeys.mockResolvedValue([key()]);
  apiMock.users.mockResolvedValue([admin, member]);
  apiMock.servers.mockResolvedValue([makeServer()]);
  apiMock.permissions.mockResolvedValue([]);
  apiMock.conversations.mockResolvedValue([]);
});

describe("keyStatus", () => {
  const now = new Date("2026-10-25T00:00:00Z");
  it("classifies keys", () => {
    expect(keyStatus(key(), now)).toBe("active");
    expect(keyStatus(key({ revoked_at: "2026-10-21T00:00:00Z" }), now)).toBe("revoked");
    expect(keyStatus(key({ expires_at: "2026-10-24T00:00:00Z" }), now)).toBe("expired");
    expect(keyStatus(key({ expires_at: "2026-11-24T00:00:00Z" }), now)).toBe("active");
    expect(keyStatus(key({ revoked_at: "2026-10-21T00:00:00Z", expires_at: "2026-10-24T00:00:00Z" }), now)).toBe("revoked");
  });
});

describe("KeysPage", () => {
  it("lists keys with only a short id, never a secret", async () => {
    renderPage(<KeysPage />);
    expect(await screen.findByText("laptop")).toBeInTheDocument();
    expect(screen.getByText("gwk_01234567…")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("shows the new key exactly once, with the connection details", async () => {
    apiMock.createKey.mockResolvedValue({ ...key({ name: "cursor" }), key: NEW_KEY });
    renderPage(<KeysPage />);
    await screen.findByText("laptop");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    await userEvent.type(screen.getByLabelText("Name"), "cursor");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create key" }));

    const reveal = await screen.findByRole("dialog", { name: /Copy your new API key/ });
    expect(within(reveal).getByTestId("issued-key")).toHaveTextContent(NEW_KEY);
    expect(within(reveal).getByText(/only time/)).toBeInTheDocument();
    expect(within(reveal).getByText(new RegExp(`Authorization: Bearer ${NEW_KEY}`))).toBeInTheDocument();

    await userEvent.click(within(reveal).getByRole("button", { name: "I have saved it" }));
    expect(document.body.textContent).not.toContain(NEW_KEY); // gone for good after closing
    expect(apiMock.createKey).toHaveBeenCalledWith("cursor", undefined);
  });

  it("sends the chosen expiry", async () => {
    apiMock.createKey.mockResolvedValue({ ...key(), key: NEW_KEY });
    renderPage(<KeysPage />);
    await screen.findByText("laptop");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    await userEvent.type(screen.getByLabelText("Name"), "temp");
    await userEvent.selectOptions(screen.getByLabelText("Expires"), "30");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create key" }));
    await waitFor(() => expect(apiMock.createKey).toHaveBeenCalledWith("temp", 30));
  });

  it("revokes only after confirmation", async () => {
    apiMock.revokeKey.mockResolvedValue(key({ revoked_at: "2026-10-25T00:00:00Z" }));
    renderPage(<KeysPage />);
    await screen.findByText("laptop");
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(apiMock.revokeKey).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Revoke key" }));
    await waitFor(() => expect(apiMock.revokeKey).toHaveBeenCalledWith("0123456789abcdef0123456789abcdef"));
  });

  it("offers no revoke button for revoked or expired keys", async () => {
    apiMock.myKeys.mockResolvedValue([key({ revoked_at: "2026-10-21T00:00:00Z" }), key({ id: 2, key_id: "f".repeat(32), expires_at: "2020-01-01T00:00:00Z" })]);
    renderPage(<KeysPage />);
    await screen.findAllByText("laptop");
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
    expect(screen.getByText("revoked")).toBeInTheDocument();
    expect(screen.getByText("expired")).toBeInTheDocument();
  });

  it("shows an empty state and an error", async () => {
    apiMock.myKeys.mockResolvedValue([]);
    const { unmount } = renderPage(<KeysPage />);
    expect(await screen.findByText("No keys yet")).toBeInTheDocument();
    unmount();
    apiMock.myKeys.mockRejectedValue(new ApiError("nope", 500));
    renderPage(<KeysPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });

  it("shows the server's validation message when creating fails", async () => {
    apiMock.createKey.mockRejectedValue(new ApiError("name: String should have at least 1 character", 422));
    renderPage(<KeysPage />);
    await screen.findByText("laptop");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    await userEvent.type(screen.getByLabelText("Name"), "x");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create key" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("at least 1 character");
  });
});

describe("TeamPage", () => {
  it("lists members and marks the current user", async () => {
    renderPage(<TeamPage />);
    const team = await screen.findByRole("table", { name: "Team members" });
    expect(within(team).getByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("you")).toBeInTheDocument();
    expect(screen.getByLabelText("Role of alice@example.com")).toHaveValue("member");
  });

  it("changes a role", async () => {
    apiMock.updateUser.mockResolvedValue({ ...member, role: "admin" });
    renderPage(<TeamPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Role of alice@example.com"), "admin");
    await waitFor(() => expect(apiMock.updateUser).toHaveBeenCalledWith(2, { role: "admin" }));
  });

  it("explains why the last admin cannot be demoted", async () => {
    apiMock.updateUser.mockRejectedValue(new ApiError("The last active admin cannot be demoted or disabled", 422));
    renderPage(<TeamPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Role of admin@example.com"), "member");
    expect(await screen.findByText(/admin@example.com: The last active admin/)).toBeInTheDocument();
  });

  it("disables a user after confirmation", async () => {
    apiMock.updateUser.mockResolvedValue({ ...member, is_active: false });
    renderPage(<TeamPage />);
    const team = await screen.findByRole("table", { name: "Team members" });
    const row = within(team).getByText("alice@example.com").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Disable" }));
    expect(apiMock.updateUser).not.toHaveBeenCalled();
    await userEvent.click(within(row).getByRole("button", { name: "Disable user" }));
    await waitFor(() => expect(apiMock.updateUser).toHaveBeenCalledWith(2, { is_active: false }));
  });

  it("invites a member", async () => {
    apiMock.createUser.mockResolvedValue({ ...member, id: 9, email: "new@example.com" });
    renderPage(<TeamPage />);
    await screen.findByRole("table", { name: "Team members" });
    await userEvent.click(screen.getByRole("button", { name: "Invite member" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Email"), "new@example.com");
    await userEvent.type(within(dialog).getByLabelText("Initial password"), "a-long-password-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(apiMock.createUser).toHaveBeenCalledWith("new@example.com", "a-long-password-1", "member"));
  });

  it("issues a key for another user and reveals it once", async () => {
    apiMock.createKeyFor.mockResolvedValue({ ...key({ name: "for alice" }), key: NEW_KEY });
    renderPage(<TeamPage />);
    const team = await screen.findByRole("table", { name: "Team members" });
    const row = within(team).getByText("alice@example.com").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Issue key" }));
    const dialog = screen.getByRole("dialog", { name: /Issue a key for alice@example.com/ });
    await userEvent.type(within(dialog).getByLabelText("Name"), "for alice");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create key" }));
    expect(await screen.findByTestId("issued-key")).toHaveTextContent(NEW_KEY);
    expect(apiMock.createKeyFor).toHaveBeenCalledWith(2, "for alice", undefined);
  });

  it("lists everyone's keys with their owners and revokes one", async () => {
    apiMock.revokeKey.mockResolvedValue(key());
    renderPage(<TeamPage />);
    const table = await screen.findByRole("table", { name: "All API keys" });
    expect(within(table).getByText("alice@example.com")).toBeInTheDocument();
    await userEvent.click(within(table).getByRole("button", { name: "Revoke" }));
    await userEvent.click(within(table).getByRole("button", { name: "Revoke key" }));
    await waitFor(() => expect(apiMock.revokeKey).toHaveBeenCalled());
  });
});

describe("ChatPage", () => {
  const reply = {
    conversation_id: 7,
    reply: "One order came in: #12.",
    steps: 2,
    tool_calls: [
      { name: "woocommerce__list_orders", arguments: { after: "2026-10-25" }, is_error: false, duration_ms: 812, result_preview: '[{"id":12}]' },
    ],
  };

  async function ask(text: string) {
    await userEvent.type(screen.getByLabelText("Message"), text);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
  }

  it("answers and shows which tools were called", async () => {
    apiMock.chat.mockResolvedValue(reply);
    renderPage(<ChatPage />);
    await ask("what orders came in today?");
    expect(await screen.findByText("One order came in: #12.")).toBeInTheDocument();
    expect(screen.getByText("what orders came in today?")).toBeInTheDocument();
    const trace = screen.getByLabelText("Tools used");
    expect(within(trace).getByText("woocommerce__list_orders")).toBeInTheDocument();
    expect(within(trace).getByText("812 ms")).toBeInTheDocument();
    expect(apiMock.chat).toHaveBeenCalledWith("what orders came in today?", undefined);
  });

  it("continues the same conversation on the next message", async () => {
    apiMock.chat.mockResolvedValue(reply);
    renderPage(<ChatPage />);
    await ask("first");
    await screen.findByText("One order came in: #12.");
    await ask("second");
    await waitFor(() => expect(apiMock.chat).toHaveBeenLastCalledWith("second", 7));
  });

  it("starts from an example prompt", async () => {
    apiMock.chat.mockResolvedValue(reply);
    renderPage(<ChatPage />);
    await userEvent.click(screen.getByRole("button", { name: "What orders came in today?" }));
    await waitFor(() => expect(apiMock.chat).toHaveBeenCalledWith("What orders came in today?", undefined));
  });

  it("explains when chat is not configured and keeps the user's text", async () => {
    apiMock.chat.mockRejectedValue(new ApiError("Chat is not configured: set GEMINI_API_KEY (and restart the gateway).", 503));
    renderPage(<ChatPage />);
    await ask("hello there");
    expect(await screen.findByRole("alert")).toHaveTextContent(/not configured.*administrator/);
    expect(screen.getByLabelText("Message")).toHaveValue("hello there");
    expect(screen.queryByText("You")).not.toBeInTheDocument(); // the unsent message was taken back out
  });

  it("flags a failed tool call", async () => {
    apiMock.chat.mockResolvedValue({
      ...reply,
      tool_calls: [{ name: "woocommerce__create_product", arguments: {}, is_error: true, duration_ms: 30, result_preview: "You are not permitted" }],
    });
    renderPage(<ChatPage />);
    await ask("create a product");
    expect(await screen.findByText("failed")).toBeInTheDocument();
  });

  it("renders message text as plain text, never as HTML", async () => {
    apiMock.chat.mockResolvedValue({ ...reply, reply: '<img src=x onerror="window.__pwned=1"><b>bold</b>', tool_calls: [] });
    renderPage(<ChatPage />);
    await ask("hi");
    expect(await screen.findByText(/<img src=x/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });

  it("does not send empty or whitespace-only messages", async () => {
    renderPage(<ChatPage />);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Message"), "   ");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("sends with Enter and keeps Shift+Enter for new lines", async () => {
    apiMock.chat.mockResolvedValue(reply);
    renderPage(<ChatPage />);
    await userEvent.type(screen.getByLabelText("Message"), "line one{Shift>}{Enter}{/Shift}line two");
    expect(apiMock.chat).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Message"), "{Enter}");
    await waitFor(() => expect(apiMock.chat).toHaveBeenCalledWith("line one\nline two", undefined));
  });

  it("opens an earlier conversation and its saved tool traces", async () => {
    apiMock.conversations.mockResolvedValue([{ id: 3, title: "Orders question", created_at: null, updated_at: null }]);
    apiMock.conversation.mockResolvedValue([
      { id: 1, role: "user", content: "orders?", tool_calls: null, created_at: null },
      { id: 2, role: "assistant", content: "Two orders.", tool_calls: reply.tool_calls, created_at: null },
    ]);
    renderPage(<ChatPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Orders question" }));
    expect(await screen.findByText("Two orders.")).toBeInTheDocument();
    expect(screen.getByLabelText("Tools used")).toBeInTheDocument();
    expect(apiMock.conversation).toHaveBeenCalledWith(3);
  });

  it("deletes a conversation after confirmation", async () => {
    apiMock.conversations.mockResolvedValue([{ id: 3, title: "Old chat", created_at: null, updated_at: null }]);
    apiMock.conversation.mockResolvedValue([]);
    apiMock.deleteConversation.mockResolvedValue(undefined);
    renderPage(<ChatPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Old chat" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete", hidden: false }));
    await waitFor(() => expect(apiMock.deleteConversation).toHaveBeenCalledWith(3));
  });
});

describe("PermissionsPage", () => {
  it("describes rules in plain words", () => {
    const emails = new Map([[2, "alice@example.com"]]);
    const base = { id: 1, created_at: null };
    expect(describeRule({ ...base, role: "member", user_id: null, server_slug: "woocommerce", tool_name: "create_product", effect: "deny" }, emails)).toBe(
      "members cannot use woocommerce__create_product",
    );
    expect(describeRule({ ...base, role: null, user_id: 2, server_slug: "docs", tool_name: null, effect: "allow" }, emails)).toBe(
      "alice@example.com can use every tool on docs",
    );
  });

  it("creates a deny rule for a role and tool", async () => {
    apiMock.setPermission.mockResolvedValue({ id: 1 });
    renderPage(<PermissionsPage />);
    const form = await screen.findByRole("form", { name: "Add a rule" });
    await waitFor(() => expect(within(form).getByRole("option", { name: "WooCommerce" })).toBeInTheDocument());
    await userEvent.selectOptions(within(form).getByLabelText("Server"), "woocommerce");
    await userEvent.selectOptions(within(form).getByLabelText("Tool"), "create_product");
    await userEvent.click(within(form).getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(apiMock.setPermission).toHaveBeenCalledWith({ role: "member", server_slug: "woocommerce", tool_name: "create_product", effect: "deny" }),
    );
  });

  it("creates a rule for one person on a whole server", async () => {
    apiMock.setPermission.mockResolvedValue({ id: 1 });
    renderPage(<PermissionsPage />);
    const form = await screen.findByRole("form", { name: "Add a rule" });
    await waitFor(() => expect(within(form).getByRole("option", { name: "WooCommerce" })).toBeInTheDocument());
    await userEvent.selectOptions(within(form).getByLabelText("Applies to"), "user");
    await userEvent.selectOptions(within(form).getByLabelText("Person"), "alice@example.com");
    await userEvent.selectOptions(within(form).getByLabelText("Server"), "woocommerce");
    await userEvent.selectOptions(within(form).getByLabelText("Effect"), "allow");
    await userEvent.click(within(form).getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(apiMock.setPermission).toHaveBeenCalledWith({ user_id: 2, server_slug: "woocommerce", tool_name: null, effect: "allow" }),
    );
  });

  it("cannot save until a server (and person) is chosen", async () => {
    renderPage(<PermissionsPage />);
    const form = await screen.findByRole("form", { name: "Add a rule" });
    expect(within(form).getByRole("button", { name: "Save rule" })).toBeDisabled();
  });

  it("lists and deletes rules", async () => {
    apiMock.permissions.mockResolvedValue([
      { id: 5, role: "member", user_id: null, server_slug: "woocommerce", tool_name: "update_order_status", effect: "deny", created_at: null },
    ]);
    apiMock.deletePermission.mockResolvedValue(undefined);
    renderPage(<PermissionsPage />);
    expect(await screen.findByText("members cannot use woocommerce__update_order_status")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete rule" }));
    await waitFor(() => expect(apiMock.deletePermission).toHaveBeenCalledWith(5));
  });
});

describe("ToolsPage", () => {
  const tools = [
    { name: "woocommerce__list_orders", description: "List orders", input_schema: { type: "object" } },
    { name: "utilities__calculate", description: "Do arithmetic", input_schema: { type: "object" } },
    { name: "woocommerce__create_product", description: "Create a product", input_schema: { type: "object" } },
  ];

  it("groups tools by server", () => {
    expect(groupByServer(tools).map(([slug, items]) => [slug, items.length])).toEqual([
      ["utilities", 1],
      ["woocommerce", 2],
    ]);
    expect(groupByServer([{ name: "plain", description: null, input_schema: {} }])[0]![0]).toBe("other");
  });

  it("filters by name or description", async () => {
    apiMock.tools.mockResolvedValue(tools);
    renderPage(<ToolsPage />);
    await screen.findByText("woocommerce__list_orders");
    await userEvent.type(screen.getByLabelText("Search tools"), "arithmetic");
    expect(screen.getByText("utilities__calculate")).toBeInTheDocument();
    expect(screen.queryByText("woocommerce__list_orders")).not.toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Search tools"));
    await userEvent.type(screen.getByLabelText("Search tools"), "zzzz");
    expect(await screen.findByText("Nothing matches that search")).toBeInTheDocument();
  });

  it("explains an empty catalogue", async () => {
    apiMock.tools.mockResolvedValue([]);
    renderPage(<ToolsPage />);
    expect(await screen.findByText("No tools available")).toBeInTheDocument();
  });
});

describe("AuditPage", () => {
  const event = (id: number, overrides = {}) => ({
    id,
    occurred_at: "2026-10-25T10:00:00Z",
    actor_user_id: 2,
    actor_email: "alice@example.com",
    channel: "mcp",
    server: "woocommerce",
    tool: "list_orders",
    operation: "tool_call",
    duration_ms: 1500,
    outcome: "success",
    error_category: null,
    ...overrides,
  });

  it("shows who, what, how long and the outcome", async () => {
    apiMock.audit.mockResolvedValue({
      items: [event(2), event(1, { outcome: "failure", error_category: "timeout", actor_email: null, channel: "chat", duration_ms: 15_000 })],
      total: 2,
      limit: 25,
      offset: 0,
    });
    renderPage(<AuditPage />);
    const table = await screen.findByRole("table", { name: "Audit log" });
    expect(within(table).getByText("alice@example.com")).toBeInTheDocument();
    expect(within(table).getByText("1.50 s")).toBeInTheDocument();
    expect(within(table).getByText("success")).toBeInTheDocument();
    expect(within(table).getByText("timeout")).toBeInTheDocument();
    expect(within(table).getByText("deleted user")).toBeInTheDocument();
    expect(within(table).getByText("chat")).toBeInTheDocument();
    expect(screen.getByText("1-2 of 2")).toBeInTheDocument();
  });

  it("filters and returns to the first page", async () => {
    apiMock.audit.mockResolvedValue({ items: [event(1)], total: 60, limit: 25, offset: 0 });
    renderPage(<AuditPage />);
    await screen.findByRole("table", { name: "Audit log" });
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(apiMock.audit).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25 })));
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "failure");
    await waitFor(() => expect(apiMock.audit).toHaveBeenLastCalledWith(expect.objectContaining({ outcome: "failure", offset: 0 })));
  });

  it("disables paging at the ends", async () => {
    apiMock.audit.mockResolvedValue({ items: [event(1)], total: 1, limit: 25, offset: 0 });
    renderPage(<AuditPage />);
    await screen.findByRole("table", { name: "Audit log" });
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("shows an empty state", async () => {
    apiMock.audit.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    renderPage(<AuditPage />);
    expect(await screen.findByText("No tool calls match")).toBeInTheDocument();
  });
});
