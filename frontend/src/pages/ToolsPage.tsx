import { useMemo, useState } from "react";
import { api, type GatewayTool } from "../lib/api";
import { pluralize } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { Empty, ErrorNote, Loading, PageHeader, TextInput } from "../components/ui";

export function groupByServer(tools: GatewayTool[]): [string, GatewayTool[]][] {
  const groups = new Map<string, GatewayTool[]>();
  for (const tool of tools) {
    const slug = tool.name.includes("__") ? tool.name.slice(0, tool.name.indexOf("__")) : "other";
    groups.set(slug, [...(groups.get(slug) ?? []), tool]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
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
        subtitle="Everything you can use through the gateway or the chat assistant, from every enabled server."
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
                </details>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
