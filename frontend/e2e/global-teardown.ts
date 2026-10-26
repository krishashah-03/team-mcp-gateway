import { execFileSync } from "node:child_process";
import path from "node:path";
import { PYTHON, BACKEND } from "./support/paths";

export default async function globalTeardown() {
  execFileSync(PYTHON, [path.resolve(import.meta.dirname, "support/e2e_data.py"), "purge"], { cwd: BACKEND });
}
