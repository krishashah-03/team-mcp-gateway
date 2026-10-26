import { useEffect, useState, type FormEvent } from "react";
import { api, slugify, type AuthType, type McpServer } from "../lib/api";
import { pluralize, timeAgo } from "../lib/format";
import { messageOf, useLoad } from "../lib/useLoad";
import {
  Badge,
  Button,
  ConfirmButton,
  Empty,
  ErrorNote,
  Loading,
  Modal,
  PageHeader,
  Select,
  TextArea,
  TextInput,
} from "../components/ui";

const STATUS_TONE = { connected: "green", unreachable: "red", disabled: "gray" } as const;
const STATUS_LABEL = { connected: "Connected", unreachable: "Unreachable", disabled: "Disabled" } as const;
const POLL_MS = 15_000;

export default function ServersPage() {
  const servers = useLoad(() => api.servers());
  const [editing, setEditing] = useState<McpServer | "new" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Keep statuses live: a server that just died should show as unreachable without a reload.
  useEffect(() => {
    const timer = setInterval(() => void servers.reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [servers.reload]);

  async function act(server: McpServer, work: () => Promise<unknown>) {
    setBusyId(server.id);
    setActionError(null);
    try {
      await work();
      await servers.reload();
    } catch (caught) {
      setActionError(`${server.name}: ${messageOf(caught)}`);
    } finally {
      setBusyId(null);
    }
  }

  // The list normally shows the gateway's latest background check; this probes every server now.
  async function refreshAll() {
    setRefreshing(true);
    setActionError(null);
    try {
      servers.setData(await api.servers(true));
    } catch (caught) {
      setActionError(messageOf(caught));
    } finally {
      setRefreshing(false);
    }
  }

  const list = servers.data ?? [];

  return (
    <>
      <PageHeader
        title="MCP servers"
        subtitle="Servers you connect once. Every team member can use their tools through the gateway."
        actions={
          <>
            <Button busy={refreshing} onClick={() => void refreshAll()}>
              Re-check all
            </Button>
            <Button variant="primary" onClick={() => setEditing("new")}>
              Add server
            </Button>
          </>
        }
      />
      <ErrorNote message={servers.error ?? actionError} />
      {servers.loading && !servers.data ? <Loading label="Checking servers" /> : null}
      {servers.data && list.length === 0 ? (
        <Empty title="No servers yet">Add an MCP server by its URL to start exposing its tools.</Empty>
      ) : null}

      <ul className="cards" aria-label="Registered MCP servers">
        {list.map((server) => (
          <li key={server.id} className="card" data-testid={`server-${server.slug}`}>
            <div className="card-head">
              <div>
                <h2>
                  {server.name} <span className="slug">{server.slug}</span>
                </h2>
                <p className="muted url">{server.endpoint_url}</p>
              </div>
              <Badge tone={STATUS_TONE[server.status]}>{STATUS_LABEL[server.status]}</Badge>
            </div>

            {server.description ? <p>{server.description}</p> : null}
            {server.status === "unreachable" && server.status_detail ? (
              <ErrorNote message={server.status_detail} />
            ) : null}

            <p className="meta">
              {server.auth_type === "none"
                ? "No credential"
                : `${server.auth_type === "bearer" ? "Bearer token" : `Header ${server.auth_header_name}`} · ${
                    server.has_credential ? "stored (hidden)" : "missing"
                  }`}
              {server.checked_at ? ` · checked ${timeAgo(server.checked_at)}` : ""}
            </p>

            {server.status !== "disabled" ? (
              <details className="tools">
                <summary>{pluralize(server.tools.length, "tool")}</summary>
                {server.tools.length === 0 ? (
                  <p className="muted">No tools reported.</p>
                ) : (
                  <ul>
                    {server.tools.map((tool) => (
                      <li key={tool.namespaced_name}>
                        <code>{tool.namespaced_name}</code>
                        {tool.description ? <span className="muted"> {tool.description}</span> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            ) : null}

            <div className="card-actions">
              <Button
                busy={busyId === server.id}
                onClick={() => act(server, () => api.updateServer(server.id, { is_enabled: !server.is_enabled }))}
              >
                {server.is_enabled ? "Disable" : "Enable"}
              </Button>
              <Button disabled={!server.is_enabled || busyId === server.id} onClick={() => act(server, () => api.refreshServer(server.id))}>
                Re-check
              </Button>
              <Button onClick={() => setEditing(server)}>Edit</Button>
              <ConfirmButton
                confirmLabel="Remove server"
                busy={busyId === server.id}
                onConfirm={() => act(server, () => api.deleteServer(server.id))}
              >
                Remove
              </ConfirmButton>
            </div>
          </li>
        ))}
      </ul>

      {editing ? (
        <ServerForm
          server={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await servers.reload();
          }}
        />
      ) : null}
    </>
  );
}

function ServerForm({
  server,
  onClose,
  onSaved,
}: {
  server: McpServer | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const editing = server !== null;
  const [name, setName] = useState(server?.name ?? "");
  const [slug, setSlug] = useState(server?.slug ?? "");
  const [slugTouched, setSlugTouched] = useState(false);
  const [url, setUrl] = useState(server?.endpoint_url ?? "");
  const [description, setDescription] = useState(server?.description ?? "");
  const [authType, setAuthType] = useState<AuthType>(server?.auth_type ?? "none");
  const [headerName, setHeaderName] = useState(server?.auth_header_name ?? "");
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const needsCredential = authType !== "none";
      if (server) {
        await api.updateServer(server.id, {
          name,
          endpoint_url: url,
          description: description || null,
          auth_type: authType,
          auth_header_name: authType === "header" ? headerName : null,
          // Left out unless typed, so the stored credential is kept.
          ...(needsCredential && credential ? { credential } : {}),
        });
      } else {
        await api.createServer({
          name,
          slug,
          endpoint_url: url,
          description: description || null,
          auth_type: authType,
          auth_header_name: authType === "header" ? headerName : null,
          credential: needsCredential ? credential : null,
        });
      }
      await onSaved();
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <Modal title={editing ? `Edit ${server.name}` : "Add MCP server"} onClose={onClose} wide>
      <form onSubmit={submit} className="form">
        <div className="grid-2">
          <TextInput
            label="Name"
            required
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              if (!editing && !slugTouched) setSlug(slugify(event.target.value));
            }}
          />
          <TextInput
            label="Slug"
            required
            disabled={editing}
            value={slug}
            maxLength={40}
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            hint="Prefix for its tools, e.g. shop__list_orders. Cannot be changed later."
            onChange={(event) => {
              setSlug(event.target.value);
              setSlugTouched(true);
            }}
          />
        </div>
        <TextInput
          label="Endpoint URL"
          type="url"
          required
          placeholder="http://my-server:8000/mcp"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <TextArea
          label="Description"
          rows={2}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <div className="grid-2">
          <Select label="Authentication" value={authType} onChange={(event) => setAuthType(event.target.value as AuthType)}>
            <option value="none">None</option>
            <option value="bearer">Bearer token</option>
            <option value="header">Custom header</option>
          </Select>
          {authType === "header" ? (
            <TextInput
              label="Header name"
              required
              placeholder="X-Api-Key"
              value={headerName}
              onChange={(event) => setHeaderName(event.target.value)}
            />
          ) : null}
        </div>
        {authType !== "none" ? (
          <TextInput
            label={editing && server.has_credential ? "New credential" : "Credential"}
            type="password"
            autoComplete="off"
            required={!editing || !server.has_credential || server.auth_type !== authType}
            value={credential}
            onChange={(event) => setCredential(event.target.value)}
            hint={
              editing && server.has_credential
                ? "Leave blank to keep the stored credential. It is never shown again."
                : "Stored encrypted on the server. It is never shown again, to you or to anyone else."
            }
          />
        ) : null}
        <ErrorNote message={error} />
        <div className="modal-actions">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={busy}>
            {editing ? "Save changes" : "Add server"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
