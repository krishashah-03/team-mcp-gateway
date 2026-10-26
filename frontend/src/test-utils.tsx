import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import type { McpServer, User } from "./lib/api";

export function renderPage(ui: ReactElement, route = "/") {
  return render(<MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>);
}

export const admin: User = { id: 1, email: "admin@example.com", role: "admin", is_active: true, created_at: "2026-10-01T00:00:00Z" };
export const member: User = { id: 2, email: "alice@example.com", role: "member", is_active: true, created_at: "2026-10-02T00:00:00Z" };

export function makeServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: 1,
    name: "WooCommerce",
    slug: "woocommerce",
    endpoint_url: "http://woo:8001/mcp",
    description: "Products and orders",
    is_enabled: true,
    auth_type: "bearer",
    auth_header_name: null,
    has_credential: true,
    status: "connected",
    status_detail: null,
    checked_at: new Date().toISOString(),
    tools: [
      { name: "list_orders", namespaced_name: "woocommerce__list_orders", description: "List orders" },
      { name: "create_product", namespaced_name: "woocommerce__create_product", description: "Create a product" },
    ],
    ...overrides,
  };
}
