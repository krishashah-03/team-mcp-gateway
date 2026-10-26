import { expect, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";
import path from "node:path";

const FIXTURE_URL = "http://127.0.0.1:8113/mcp";
const SERVER_NAME = "Tst E2E Fixture";
const SLUG = "tst-e2e-fixture"; // "tst" prefix: the purge script removes it afterwards
const MEMBER_PASSWORD = "E2e-member-password-1";
const SHOTS = path.resolve(import.meta.dirname, "screenshots");

const state: { memberEmail: string; memberKey: string } = {
  memberEmail: `e2e-member-${Math.random().toString(16).slice(2, 8)}@test.invalid`,
  memberKey: "",
};

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function newSession(browser: Browser, email: string, password: string, colorScheme: "light" | "dark" = "light") {
  const context = await browser.newContext({ colorScheme, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await signIn(page, email, password);
  return { context, page };
}

/** Call the gateway's MCP endpoint through the dev proxy, like an external client would. */
async function mcp(request: APIRequestContext, key: string, method: string, params?: object) {
  const response = await request.post("/mcp", {
    headers: { Authorization: `Bearer ${key}`, Accept: "application/json, text/event-stream" },
    data: { jsonrpc: "2.0", id: 1, method, params },
  });
  const body = await response.text();
  return { status: response.status(), body };
}

test.describe.configure({ mode: "serial" });

test("a wrong password is refused with a clear message", async ({ page }) => {
  await signIn(page, process.env.E2E_ADMIN_EMAIL!, "not-the-password-1");
  await expect(page.getByRole("alert")).toHaveText("Invalid email or password");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "01-login-error.png") });
});

test("an admin registers an MCP server and sees it connect", async ({ browser }) => {
  const { context, page } = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  await expect(page.getByRole("heading", { name: "MCP servers" })).toBeVisible();

  await page.getByRole("button", { name: "Add server" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill(SERVER_NAME);
  await expect(dialog.getByLabel("Slug")).toHaveValue(SLUG);
  await dialog.getByLabel("Endpoint URL").fill(FIXTURE_URL);
  await dialog.getByRole("button", { name: "Add server" }).click();

  const card = page.getByTestId(`server-${SLUG}`);
  await expect(card.getByText("Connected")).toBeVisible();
  await expect(card.getByText("3 tools")).toBeVisible();
  await card.getByText("3 tools").click();
  await expect(card.getByText(`${SLUG}__echo`)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "02-servers.png"), fullPage: true });
  await context.close();
});

test("the server list rejects a dangerous destination", async ({ browser }) => {
  const { context, page } = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "Add server" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("Tst Evil");
  await dialog.getByLabel("Endpoint URL").fill("http://169.254.169.254/latest/meta-data");
  await dialog.getByRole("button", { name: "Add server" }).click();
  await expect(dialog.getByRole("alert")).toContainText("not allowed");
  await expect(dialog).toBeVisible(); // stays open so the admin can fix it
  await page.screenshot({ path: path.join(SHOTS, "03-ssrf-refused.png") });
  await context.close();
});

test("an admin invites a member, who sees only member pages", async ({ browser }) => {
  const admin = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  await admin.page.getByRole("link", { name: "Team" }).click();
  await admin.page.getByRole("button", { name: "Invite member" }).click();
  const dialog = admin.page.getByRole("dialog");
  await dialog.getByLabel("Email").fill(state.memberEmail);
  await dialog.getByLabel("Initial password").fill(MEMBER_PASSWORD);
  await dialog.getByRole("button", { name: "Create account" }).click();
  await expect(admin.page.getByRole("table", { name: "Team members" }).getByText(state.memberEmail)).toBeVisible();
  await admin.page.screenshot({ path: path.join(SHOTS, "04-team.png"), fullPage: true });
  await admin.context.close();

  const member = await newSession(browser, state.memberEmail, MEMBER_PASSWORD);
  const nav = member.page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Chat" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "MCP servers" })).toHaveCount(0);
  await member.page.goto("/servers");
  await expect(member.page.getByText("Admins only")).toBeVisible();
  await member.context.close();
});

test("a member creates a key, which is shown once and works against /mcp", async ({ browser, request }) => {
  const { context, page } = await newSession(browser, state.memberEmail, MEMBER_PASSWORD);
  await page.getByRole("link", { name: "My API keys" }).click();
  await page.getByRole("button", { name: "Create key" }).click();
  await page.getByRole("dialog").getByLabel("Name").fill("e2e laptop");
  await page.getByRole("dialog").getByRole("button", { name: "Create key" }).click();

  const reveal = page.getByRole("dialog", { name: /Copy your new API key/ });
  state.memberKey = (await reveal.getByTestId("issued-key").textContent())!.trim();
  expect(state.memberKey).toMatch(/^gwk_[0-9a-f]{32}_[A-Za-z0-9_-]{43}$/);
  await page.screenshot({ path: path.join(SHOTS, "05-key-reveal.png") });
  await reveal.getByRole("button", { name: "I have saved it" }).click();

  // After closing, the secret is nowhere on the page, only the short id remains.
  await expect(page.locator("body")).not.toContainText(state.memberKey);
  await expect(page.getByText(`gwk_${state.memberKey.split("_")[1]!.slice(0, 8)}…`)).toBeVisible();

  const listed = await mcp(request, state.memberKey, "tools/list");
  expect(listed.status).toBe(200);
  expect(listed.body).toContain(`${SLUG}__echo`);
  const called = await mcp(request, state.memberKey, "tools/call", { name: `${SLUG}__echo`, arguments: { text: "from e2e" } });
  expect(called.status).toBe(200);
  expect(called.body).toContain("from e2e");
  await context.close();
});

test("the member's calls show up in the admin's audit log", async ({ browser }) => {
  const { context, page } = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByLabel("Server").fill(SLUG);
  const row = page.getByRole("row").filter({ hasText: state.memberEmail }).filter({ hasText: "echo" }).first();
  await expect(row).toContainText("success");
  await expect(row).toContainText(SLUG);
  await page.screenshot({ path: path.join(SHOTS, "06-audit.png"), fullPage: true });
  await context.close();
});

test("a deny rule hides a tool from the member and blocks calling it", async ({ browser, request }) => {
  const admin = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  await admin.page.getByRole("link", { name: "Permissions" }).click();
  const form = admin.page.getByRole("form", { name: "Add a rule" });
  await expect(form.getByRole("option", { name: SERVER_NAME })).toBeAttached();
  await form.getByLabel("Server").selectOption(SLUG);
  await form.getByLabel("Tool").selectOption("echo");
  await form.getByRole("button", { name: "Save rule" }).click();
  await expect(admin.page.getByText(`members cannot use ${SLUG}__echo`)).toBeVisible();
  await admin.page.screenshot({ path: path.join(SHOTS, "07-permissions.png"), fullPage: true });

  const listed = await mcp(request, state.memberKey, "tools/list");
  expect(listed.body).not.toContain(`${SLUG}__echo`);
  expect(listed.body).toContain(`${SLUG}__list_orders`);
  const refused = await mcp(request, state.memberKey, "tools/call", { name: `${SLUG}__echo`, arguments: { text: "sneaky" } });
  expect(refused.body).toContain("not permitted");

  const member = await newSession(browser, state.memberEmail, MEMBER_PASSWORD, "dark");
  await member.page.getByRole("link", { name: "Tools" }).click();
  await expect(member.page.getByText(`${SLUG}__list_orders`)).toBeVisible();
  await expect(member.page.getByText(`${SLUG}__echo`)).toHaveCount(0);
  await member.page.screenshot({ path: path.join(SHOTS, "08-member-tools-dark.png"), fullPage: true });
  await member.context.close();

  // Remove the rule again through the UI.
  await admin.page.getByRole("row").filter({ hasText: `${SLUG}__echo` }).getByRole("button", { name: "Delete" }).click();
  await admin.page.getByRole("button", { name: "Delete rule" }).click();
  await expect(admin.page.getByText(`members cannot use ${SLUG}__echo`)).toHaveCount(0);
  expect((await mcp(request, state.memberKey, "tools/list")).body).toContain(`${SLUG}__echo`);
  await admin.context.close();
});

test("disabling a server removes its tools, and enabling brings them back", async ({ browser, request }) => {
  const { context, page } = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  const card = page.getByTestId(`server-${SLUG}`);
  await card.getByRole("button", { name: "Disable" }).click();
  await expect(card.getByText("Disabled")).toBeVisible();
  expect((await mcp(request, state.memberKey, "tools/list")).body).not.toContain(`${SLUG}__echo`);

  await card.getByRole("button", { name: "Enable" }).click();
  await expect(card.getByText("Connected")).toBeVisible();
  expect((await mcp(request, state.memberKey, "tools/list")).body).toContain(`${SLUG}__echo`);
  await context.close();
});

test("chat explains clearly when no model is configured", async ({ browser }) => {
  const { context, page } = await newSession(browser, state.memberEmail, MEMBER_PASSWORD);
  await page.getByLabel("Message").fill("what orders came in today?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("alert")).toContainText("not configured");
  await expect(page.getByLabel("Message")).toHaveValue("what orders came in today?"); // not lost
  await page.screenshot({ path: path.join(SHOTS, "09-chat-unconfigured.png") });
  await context.close();
});

test("revoking a key in the dashboard rejects the member's next call", async ({ browser, request }) => {
  const { context, page } = await newSession(browser, state.memberEmail, MEMBER_PASSWORD);
  await page.getByRole("link", { name: "My API keys" }).click();
  await page.getByRole("button", { name: "Revoke" }).click();
  await page.getByRole("button", { name: "Revoke key" }).click();
  await expect(page.getByText("revoked")).toBeVisible();

  expect((await mcp(request, state.memberKey, "tools/list")).status).toBe(401);
  await context.close();
});

test("a removed server disappears, and a signed-out session cannot reach the API", async ({ browser, page }) => {
  const { context, page: adminPage } = await newSession(browser, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  const card = adminPage.getByTestId(`server-${SLUG}`);
  await card.getByRole("button", { name: "Remove" }).click();
  await card.getByRole("button", { name: "Remove server" }).click();
  await expect(card).toHaveCount(0);
  await adminPage.getByRole("button", { name: "Sign out" }).click();
  await expect(adminPage.getByRole("button", { name: "Sign in" })).toBeVisible();
  await context.close();

  await page.goto("/audit");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  const anonymous = await page.request.get("/api/v1/audit");
  expect(anonymous.status()).toBe(401);
});

test("the dashboard works on a phone-sized screen", async ({ browser }: { browser: Browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 800 } });
  const page = await context.newPage();
  await signIn(page, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
  await expect(page.getByRole("heading", { name: "MCP servers" })).toBeVisible();
  await expect(page.getByText("Checking servers")).toHaveCount(0);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false); // no sideways scrolling
  await page.screenshot({ path: path.join(SHOTS, "10-mobile.png") });
  await context.close();
});
