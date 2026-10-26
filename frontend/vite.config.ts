import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the dashboard and the API share an origin through this proxy, so the
// browser never makes a cross-origin request and no CORS setup is needed.
const GATEWAY = process.env.GATEWAY_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": GATEWAY,
      "/mcp": GATEWAY,
      "/lite": GATEWAY,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
