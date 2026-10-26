import path from "node:path";

export const BACKEND = path.resolve(import.meta.dirname, "../../../backend");
// The backend's virtualenv interpreter; override with E2E_PYTHON on other platforms.
export const PYTHON =
  process.env.E2E_PYTHON ??
  path.join(BACKEND, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
