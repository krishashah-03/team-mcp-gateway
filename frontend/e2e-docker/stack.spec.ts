import { expect, test, type Page } from "@playwright/test";

const EMAIL = process.env.STACK_ADMIN_EMAIL!;
const PASSWORD = process.env.STACK_ADMIN_PASSWORD!;

/** Collect anything the browser complains about, in particular Content-Security-Policy violations. */
function watchForProblems(page: Page): string[] {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" || /content security policy/i.test(message.text())) problems.push(message.text());
  });
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  return problems;
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "MCP servers" })).toBeVisible();
}

test("every page works under the strict Content-Security-Policy", async ({ page }) => {
  const problems = watchForProblems(page);
  await signIn(page);

  for (const [link, heading] of [
    ["MCP servers", "MCP servers"],
    ["Team", "Team"],
    ["Permissions", "Permissions"],
    ["Audit log", "Audit log"],
    ["My API keys", "My API keys"],
    ["Tools", "Tools"],
  ] as const) {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
  }
  await page.getByRole("link", { name: "Chat" }).click();
  await expect(page.getByText("Ask about your store")).toBeVisible();
  expect(problems).toEqual([]);
});

test("the stack's three servers are connected with their tools", async ({ page }) => {
  await signIn(page);
  for (const [slug, tools] of [["woocommerce", "5 tools"], ["utilities", "3 tools"], ["docs", "3 tools"]] as const) {
    const card = page.getByTestId(`server-${slug}`);
    await expect(card.getByText("Connected")).toBeVisible();
    await expect(card.getByText(tools)).toBeVisible();
  }
});

test("a deep link survives a reload (single-page-app fallback)", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
});

test("a key made in the browser works from an MCP client, and the call is audited", async ({ page, request }) => {
  await signIn(page);
  await page.getByRole("link", { name: "My API keys" }).click();
  await page.getByRole("button", { name: "Create key" }).click();
  await page.getByRole("dialog").getByLabel("Name").fill("stack e2e");
  await page.getByRole("dialog").getByRole("button", { name: "Create key" }).click();
  const key = (await page.getByTestId("issued-key").textContent())!.trim();
  await page.getByRole("button", { name: "I have saved it" }).click();

  const rpc = async (body: object) => {
    const response = await request.post("/mcp", {
      headers: { Authorization: `Bearer ${key}`, Accept: "application/json, text/event-stream" },
      data: { jsonrpc: "2.0", id: 1, ...body },
    });
    return { status: response.status(), text: await response.text() };
  };
  const listed = await rpc({ method: "tools/list" });
  expect(listed.status).toBe(200);
  for (const tool of ["woocommerce__list_orders", "utilities__calculate", "docs__search_documents"]) {
    expect(listed.text).toContain(tool);
  }
  const called = await rpc({ method: "tools/call", params: { name: "utilities__convert_units", arguments: { value: 10, from_unit: "km", to_unit: "mi" } } });
  expect(called.text).toContain("6.213712");

  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByLabel("Server").fill("utilities");
  await expect(page.getByRole("row").filter({ hasText: "convert_units" }).filter({ hasText: EMAIL }).first()).toContainText("success");
});

test("chat explains that no model is configured", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Chat" }).click();
  await page.getByLabel("Message").fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("alert")).toContainText("not configured");
});
