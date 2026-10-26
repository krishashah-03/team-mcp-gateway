import { useState, type FormEvent } from "react";
import { api, type ApiKey, type IssuedApiKey } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { messageOf, useLoad } from "../lib/useLoad";
import {
  Badge,
  Button,
  ConfirmButton,
  Empty,
  ErrorNote,
  KeyRevealModal,
  Loading,
  Modal,
  PageHeader,
  Select,
  TextInput,
} from "../components/ui";

export function keyStatus(key: ApiKey, now: Date = new Date()): "active" | "revoked" | "expired" {
  if (key.revoked_at) return "revoked";
  if (key.expires_at && new Date(key.expires_at) <= now) return "expired";
  return "active";
}

const TONE = { active: "green", revoked: "red", expired: "gray" } as const;

export function KeyStatusBadge({ apiKey }: { apiKey: ApiKey }) {
  const status = keyStatus(apiKey);
  return <Badge tone={TONE[status]}>{status}</Badge>;
}

export const gatewayUrl = () => `${window.location.origin}/mcp`;

export default function KeysPage() {
  const keys = useLoad(() => api.myKeys());
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<IssuedApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function revoke(key: ApiKey) {
    setError(null);
    try {
      await api.revokeKey(key.key_id);
      await keys.reload();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  return (
    <>
      <PageHeader
        title="My API keys"
        subtitle="Use a key to connect Claude Code, Cursor or any MCP client to the gateway. You never need the credentials of the servers behind it."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create key
          </Button>
        }
      />

      <section className="panel">
        <h2>Connect an MCP client</h2>
        <dl className="kv">
          <dt>Gateway URL</dt>
          <dd>
            <code>{gatewayUrl()}</code>
          </dd>
          <dt>Header</dt>
          <dd>
            <code>Authorization: Bearer &lt;your key&gt;</code>
          </dd>
          <dt>Large catalogue?</dt>
          <dd>
            Use <code>{window.location.origin}/lite/mcp</code> to expose only search, describe and run tools.
          </dd>
        </dl>
      </section>

      <ErrorNote message={keys.error ?? error} />
      {keys.loading && !keys.data ? <Loading /> : null}
      {keys.data && keys.data.length === 0 ? (
        <Empty title="No keys yet">Create a key to connect an MCP client.</Empty>
      ) : null}
      {keys.data && keys.data.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Key ID</th>
              <th>Created</th>
              <th>Expires</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.data.map((key) => (
              <tr key={key.key_id}>
                <td>{key.name}</td>
                <td>
                  <code>gwk_{key.key_id.slice(0, 8)}…</code>
                </td>
                <td>{formatDateTime(key.created_at)}</td>
                <td>{key.expires_at ? formatDateTime(key.expires_at) : "Never"}</td>
                <td>
                  <KeyStatusBadge apiKey={key} />
                </td>
                <td className="right">
                  {keyStatus(key) === "active" ? (
                    <ConfirmButton confirmLabel="Revoke key" onConfirm={() => revoke(key)}>
                      Revoke
                    </ConfirmButton>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {creating ? (
        <CreateKeyModal
          create={(name, days) => api.createKey(name, days)}
          onClose={() => setCreating(false)}
          onCreated={async (key) => {
            setCreating(false);
            setIssued(key);
            await keys.reload();
          }}
        />
      ) : null}
      {issued ? <KeyRevealModal apiKey={issued.key} name={issued.name} gatewayUrl={gatewayUrl()} onClose={() => setIssued(null)} /> : null}
    </>
  );
}

export function CreateKeyModal({
  title = "Create API key",
  create,
  onClose,
  onCreated,
}: {
  title?: string;
  create: (name: string, expiresInDays?: number) => Promise<IssuedApiKey>;
  onClose: () => void;
  onCreated: (key: IssuedApiKey) => Promise<void> | void;
}) {
  const [name, setName] = useState("");
  const [days, setDays] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreated(await create(name.trim(), days ? Number(days) : undefined));
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <TextInput
          label="Name"
          required
          maxLength={200}
          placeholder="e.g. Cursor on my laptop"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Select label="Expires" value={days} onChange={(event) => setDays(event.target.value)}>
          <option value="">Never</option>
          <option value="30">After 30 days</option>
          <option value="90">After 90 days</option>
          <option value="365">After 1 year</option>
        </Select>
        <ErrorNote message={error} />
        <div className="modal-actions">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={busy}>
            Create key
          </Button>
        </div>
      </form>
    </Modal>
  );
}
