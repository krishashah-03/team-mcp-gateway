import { defineConfig } from "@playwright/test";

// Runs against an already-running full stack (see docs/docker.md), through the real nginx.
//   STACK_URL=http://127.0.0.1:8080 STACK_ADMIN_EMAIL=... STACK_ADMIN_PASSWORD=... npm run test:e2e:stack
export default defineConfig({
  testDir: "./e2e-docker",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.STACK_URL ?? "http://127.0.0.1:8080", trace: "retain-on-failure" },
});
