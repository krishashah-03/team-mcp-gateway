import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { makeServer, renderPage } from "../test-utils";
import ServersPage from "./ServersPage";

const apiMock = vi.hoisted(() => ({
  servers: vi.fn(),
  createServer: vi.fn(),
  updateServer: vi.fn(),
  deleteServer: vi.fn(),
  refreshServer: vi.fn(),
}));

vi.mock("../lib/api", async (original) => ({ ...(await original<typeof import("../lib/api")>()), api: apiMock }));

const dead = makeServer({
  id: 2,
  name: "Docs",
  slug: "docs",
  status: "unreachable",
  status_detail: "Server 'docs' is unreachable",
  auth_type: "none",
  has_credential: false,
  tools: [],
});
const off = makeServer({ id: 3, name: "Utilities", slug: "utilities", is_enabled: false, status: "disabled", tools: [] });

beforeEach(() => {
  vi.resetAllMocks();
  apiMock.servers.mockResolvedValue([makeServer(), dead, off]);
});

describe("ServersPage", () => {
  it("shows each server with its status and tools", async () => {
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    expect(within(woo).getByText("Connected")).toBeInTheDocument();
    expect(within(woo).getByText("2 tools")).toBeInTheDocument();
    expect(within(woo).getByText("woocommerce__list_orders")).toBeInTheDocument();

    const docs = screen.getByTestId("server-docs");
    expect(within(docs).getByText("Unreachable")).toBeInTheDocument();
    expect(within(docs).getByRole("alert")).toHaveTextContent("Server 'docs' is unreachable");

    const utilities = screen.getByTestId("server-utilities");
    expect(within(utilities).getByText("Disabled")).toBeInTheDocument();
    expect(within(utilities).queryByText(/tools?$/)).not.toBeInTheDocument(); // no tool list for a disabled server
  });

  it("says a credential exists without ever showing it", async () => {
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    expect(within(woo).getByText(/Bearer token · stored \(hidden\)/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Bearer [A-Za-z0-9_-]{10,}/);
  });

  it("shows an empty state", async () => {
    apiMock.servers.mockResolvedValue([]);
    renderPage(<ServersPage />);
    expect(await screen.findByText("No servers yet")).toBeInTheDocument();
  });

  it("reports a load failure", async () => {
    apiMock.servers.mockRejectedValue(new ApiError("Cannot reach the gateway. Check that it is running.", 0));
    renderPage(<ServersPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the gateway");
  });

  it("disables a server", async () => {
    apiMock.updateServer.mockResolvedValue(makeServer({ is_enabled: false }));
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    await userEvent.click(within(woo).getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(apiMock.updateServer).toHaveBeenCalledWith(1, { is_enabled: false }));
    expect(apiMock.servers).toHaveBeenCalledTimes(2); // reloaded afterwards
  });

  it("enables a disabled server", async () => {
    apiMock.updateServer.mockResolvedValue(makeServer());
    renderPage(<ServersPage />);
    const utilities = await screen.findByTestId("server-utilities");
    await userEvent.click(within(utilities).getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(apiMock.updateServer).toHaveBeenCalledWith(3, { is_enabled: true }));
  });

  it("asks before removing and can be cancelled", async () => {
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    await userEvent.click(within(woo).getByRole("button", { name: "Remove" }));
    expect(apiMock.deleteServer).not.toHaveBeenCalled();
    await userEvent.click(within(woo).getByRole("button", { name: "Cancel" }));
    expect(within(woo).getByRole("button", { name: "Remove" })).toBeInTheDocument();
    expect(apiMock.deleteServer).not.toHaveBeenCalled();
  });

  it("removes a server after confirmation", async () => {
    apiMock.deleteServer.mockResolvedValue(undefined);
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    await userEvent.click(within(woo).getByRole("button", { name: "Remove" }));
    await userEvent.click(within(woo).getByRole("button", { name: "Remove server" }));
    await waitFor(() => expect(apiMock.deleteServer).toHaveBeenCalledWith(1));
  });

  it("re-checks every server live when asked", async () => {
    renderPage(<ServersPage />);
    await screen.findByTestId("server-woocommerce");
    expect(apiMock.servers).toHaveBeenLastCalledWith(); // normal loads use the background check
    apiMock.servers.mockResolvedValue([makeServer({ status: "unreachable", status_detail: "now down" })]);
    await userEvent.click(screen.getByRole("button", { name: "Re-check all" }));
    await waitFor(() => expect(apiMock.servers).toHaveBeenLastCalledWith(true));
    expect(await screen.findByText("now down")).toBeInTheDocument();
  });

  it("re-checks one server", async () => {
    apiMock.refreshServer.mockResolvedValue(dead);
    renderPage(<ServersPage />);
    const docs = await screen.findByTestId("server-docs");
    await userEvent.click(within(docs).getByRole("button", { name: "Re-check" }));
    await waitFor(() => expect(apiMock.refreshServer).toHaveBeenCalledWith(2));
  });

  it("surfaces an action failure with the server name", async () => {
    apiMock.updateServer.mockRejectedValue(new ApiError("boom", 500));
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    await userEvent.click(within(woo).getByRole("button", { name: "Disable" }));
    expect(await screen.findByText("WooCommerce: boom")).toBeInTheDocument();
  });
});

describe("Add server form", () => {
  async function open() {
    renderPage(<ServersPage />);
    await screen.findByTestId("server-woocommerce");
    await userEvent.click(screen.getByRole("button", { name: "Add server" }));
    return screen.getByRole("dialog");
  }

  it("fills the slug from the name until it is edited by hand", async () => {
    const dialog = await open();
    const name = within(dialog).getByLabelText("Name");
    const slug = within(dialog).getByLabelText("Slug");
    await userEvent.type(name, "My Test Server");
    expect(slug).toHaveValue("my-test-server");
    await userEvent.clear(slug);
    await userEvent.type(slug, "custom");
    await userEvent.type(name, " 2");
    expect(slug).toHaveValue("custom");
  });

  it("only asks for credential fields when authentication is on", async () => {
    const dialog = await open();
    expect(within(dialog).queryByLabelText("Credential")).not.toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText("Authentication"), "bearer");
    const credential = within(dialog).getByLabelText("Credential");
    expect(credential).toHaveAttribute("type", "password"); // masked, never shown
    expect(within(dialog).queryByLabelText("Header name")).not.toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText("Authentication"), "header");
    expect(within(dialog).getByLabelText("Header name")).toBeInTheDocument();
  });

  it("creates a server with a bearer credential", async () => {
    apiMock.createServer.mockResolvedValue(makeServer());
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Name"), "Shop");
    await userEvent.type(within(dialog).getByLabelText("Endpoint URL"), "http://shop:9000/mcp");
    await userEvent.selectOptions(within(dialog).getByLabelText("Authentication"), "bearer");
    await userEvent.type(within(dialog).getByLabelText("Credential"), "s3cret");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add server" }));
    await waitFor(() =>
      expect(apiMock.createServer).toHaveBeenCalledWith({
        name: "Shop",
        slug: "shop",
        endpoint_url: "http://shop:9000/mcp",
        description: null,
        auth_type: "bearer",
        auth_header_name: null,
        credential: "s3cret",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("sends no credential for an unauthenticated server", async () => {
    apiMock.createServer.mockResolvedValue(makeServer());
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Name"), "Open");
    await userEvent.type(within(dialog).getByLabelText("Endpoint URL"), "http://open:9000/mcp");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(apiMock.createServer).toHaveBeenCalled());
    expect(apiMock.createServer.mock.calls[0]![0]).toMatchObject({ auth_type: "none", credential: null });
  });

  it("keeps the dialog open and shows the server's error", async () => {
    apiMock.createServer.mockRejectedValue(new ApiError("Cloud metadata endpoints are not allowed", 422));
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Name"), "Evil");
    await userEvent.type(within(dialog).getByLabelText("Endpoint URL"), "http://169.254.169.254/");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add server" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Cloud metadata endpoints are not allowed");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes with Escape", async () => {
    await open();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Edit server form", () => {
  async function openEdit() {
    renderPage(<ServersPage />);
    const woo = await screen.findByTestId("server-woocommerce");
    await userEvent.click(within(woo).getByRole("button", { name: "Edit" }));
    return screen.getByRole("dialog");
  }

  it("leaves the stored credential alone when the field is blank", async () => {
    apiMock.updateServer.mockResolvedValue(makeServer());
    const dialog = await openEdit();
    expect(within(dialog).getByLabelText("Slug")).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText("Name"));
    await userEvent.type(within(dialog).getByLabelText("Name"), "Shop v2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(apiMock.updateServer).toHaveBeenCalled());
    const [id, body] = apiMock.updateServer.mock.calls[0]!;
    expect(id).toBe(1);
    expect(body.name).toBe("Shop v2");
    expect("credential" in body).toBe(false); // omitted, so the server keeps what it has
  });

  it("replaces the credential when a new one is typed", async () => {
    apiMock.updateServer.mockResolvedValue(makeServer());
    const dialog = await openEdit();
    await userEvent.type(within(dialog).getByLabelText("New credential"), "rotated");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(apiMock.updateServer.mock.calls[0]![1]).toMatchObject({ credential: "rotated" }));
  });

  it("requires a credential when switching a server from none to bearer", async () => {
    renderPage(<ServersPage />);
    const docs = await screen.findByTestId("server-docs");
    await userEvent.click(within(docs).getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Authentication"), "bearer");
    expect(within(dialog).getByLabelText("Credential")).toBeRequired();
  });
});
