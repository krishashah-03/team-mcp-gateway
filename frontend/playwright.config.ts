import { defineConfig } from "@playwright/test";
import { BACKEND, PYTHON } from "./e2e/support/paths";

const GATEWAY_PORT = 8010;
const FIXTURE_PORT = 8113;
const WEB_PORT = 5174;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      // The gateway, on its own port. An empty GEMINI_API_KEY guarantees the tests never
      // call a real model, even if the developer has a key configured.
      command: `"${PYTHON}" -m uvicorn app.main:app --port ${GATEWAY_PORT}`,
      cwd: BACKEND,
      url: `http://127.0.0.1:${GATEWAY_PORT}/health`,
      env: { GEMINI_API_KEY: "", OPENROUTER_API_KEY: "" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `"${PYTHON}" tests/fixtures/fixture_server.py ${FIXTURE_PORT}`,
      cwd: BACKEND,
      port: FIXTURE_PORT,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { GATEWAY_URL: `http://127.0.0.1:${GATEWAY_PORT}` },
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
