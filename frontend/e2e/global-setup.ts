import { execFileSync } from "node:child_process";
import path from "node:path";
import { PYTHON, BACKEND } from "./support/paths";

export default async function globalSetup() {
  const script = path.resolve(import.meta.dirname, "support/e2e_data.py");
  const output = execFileSync(PYTHON, [script, "seed"], { cwd: BACKEND, encoding: "utf-8" });
  const credentials = JSON.parse(output.trim().split("\n").pop()!);
  process.env.E2E_ADMIN_EMAIL = credentials.email;
  process.env.E2E_ADMIN_PASSWORD = credentials.password;
}
