import { useMemo, useState } from "react";
import { api, type GatewayTool } from "../lib/api";
import { pluralize } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { Badge, Button, Empty, ErrorNote, Loading, PageHeader, TextArea, TextInput } from "../components/ui";

export function groupByServer(tools: GatewayTool[]): [string, GatewayTool[]][] {
  const groups = new Map<string, GatewayTool[]>();
  for (const tool of tools) {
    const slug = tool.name.includes("__") ? tool.name.slice(0, tool.name.indexOf("__")) : "other";
    groups.set(slug, [...(groups.get(slug) ?? []), tool]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/** A starting point for the arguments: every required property with a placeholder of the right type. */
export function exampleArguments(schema: Record<string, unknown>): string {
  const properties = (schema.properties ?? {}) as Record<string, { type?: string; default?: unknown }>;
  const required = Array.isArray(schema.required) ? (schema.required as string[]) : [];
  const example: Record<string, unknown> = {};
  for (const key of required) {
    const spec = properties[key] ?? {};
    example[key] = spec.default ?? (spec.type === "integer" || spec.type === "number" ? 1 : spec.type === "boolean" ? false : "");
  }
  return JSON.stringify(example, null, 2);
}

function RunTool({ tool }: { tool: GatewayTool }) {
  const [text, setText] = useState(() => exampleArguments(tool.input_schema));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ is_error: boolean; text: string } | null>(null);

  async function run() {
    setError(null);
    setResult(null);
    let args: unknown;
    try {
      args = text.trim() ? JSON.parse(text) : {};
    } catch {
      setError("The arguments are not valid JSON.");
      return;
    }
    if (typeof args !== "object" || args === null || Array.isArray(args)) {
      setError("The arguments must be a JSON object, like {\"name\": \"value\"}.");
      return;
    }
    setBusy(true);
    try {
      setResult(await api.callTool(tool.name, args as Record<string, unknown>));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The call failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="run-tool">
      <TextArea
        label={`Arguments for ${tool.name} (JSON)`}
        rows={Math.min(10, text.split("\n").length + 1)}
        value={text}
        onChange={(event) => setText(event.target.value)}
        spellCheck={false}
      />
      <Button onClick={run} disabled={busy}>
        {busy ? "Running…" : "Run tool"}
      </Button>
      <ErrorNote message={error} />
      {result ? (
        <div role="status">
          <Badge tone={result.is_error ? "red" : "green"}>{result.is_error ? "Error" : "Success"}</Badge>
          <pre className="schema">{result.text || "(no output)"}</pre>
        </div>
      ) : null}
    </div>
  );
}

export default function ToolsPage() {
  const tools = useLoad(() => api.tools());
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matching = (tools.data ?? []).filter(
      (tool) => !needle || tool.name.toLowerCase().includes(needle) || (tool.description ?? "").toLowerCase().includes(needle),
    );
    return groupByServer(matching);
  }, [tools.data, query]);

  return (
    <>
      <PageHeader
        title="Tools"
        subtitle="Everything you can use through the gateway or the chat assistant. Open a tool and press Run to try it directly."
      />
      <TextInput
        label="Search tools"
        placeholder="e.g. orders"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <ErrorNote message={tools.error} />
      {tools.loading && !tools.data ? <Loading /> : null}
      {tools.data && tools.data.length === 0 ? (
        <Empty title="No tools available">No server is connected, or you are not permitted to use any of its tools.</Empty>
      ) : null}
      {tools.data && tools.data.length > 0 && groups.length === 0 ? <Empty title="Nothing matches that search" /> : null}

      {groups.map(([slug, items]) => (
        <section key={slug} className="panel" aria-label={`${slug} tools`}>
          <h2>
            {slug} <span className="muted">{pluralize(items.length, "tool")}</span>
          </h2>
          <ul className="tool-list">
            {items.map((tool) => (
              <li key={tool.name}>
                <details>
                  <summary>
                    <code>{tool.name}</code>
                  </summary>
                  {tool.description ? <p>{tool.description}</p> : null}
                  <pre className="schema">{JSON.stringify(tool.input_schema, null, 2)}</pre>
                  <RunTool tool={tool} />
                </details>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
