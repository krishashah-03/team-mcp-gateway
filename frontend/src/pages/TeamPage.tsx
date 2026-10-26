import { useState, type FormEvent } from "react";
import { api, type IssuedApiKey, type Role, type User } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { messageOf, useLoad } from "../lib/useLoad";
import { useAuth } from "../auth";
import {
  Badge,
  Button,
  ConfirmButton,
  ErrorNote,
  KeyRevealModal,
  Loading,
  Modal,
  PageHeader,
  Select,
  TextInput,
} from "../components/ui";
import { CreateKeyModal, KeyStatusBadge, gatewayUrl, keyStatus } from "./KeysPage";

export default function TeamPage() {
  const { user: me } = useAuth();
  const users = useLoad(() => api.users());
  const keys = useLoad(() => api.allKeys());
  const [inviting, setInviting] = useState(false);
  const [keyFor, setKeyFor] = useState<User | null>(null);
  const [issued, setIssued] = useState<IssuedApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function update(user: User, change: { role?: Role; is_active?: boolean }) {
    setError(null);
    try {
      await api.updateUser(user.id, change);
      await Promise.all([users.reload(), keys.reload()]);
    } catch (caught) {
      setError(`${user.email}: ${messageOf(caught)}`);
    }
  }

  async function revoke(keyId: string) {
    setError(null);
    try {
      await api.revokeKey(keyId);
      await keys.reload();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  return (
    <>
      <PageHeader
        title="Team"
        subtitle="Invite people, set their role, and issue or revoke their API keys."
        actions={
          <Button variant="primary" onClick={() => setInviting(true)}>
            Invite member
          </Button>
        }
      />
      <ErrorNote message={users.error ?? keys.error ?? error} />
      {users.loading && !users.data ? <Loading /> : null}

      {users.data ? (
        <table className="table" aria-label="Team members">
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Joined</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.data.map((user) => (
              <tr key={user.id}>
                <td>
                  {user.email} {user.id === me?.id ? <Badge tone="gray">you</Badge> : null}
                </td>
                <td>
                  <select
                    aria-label={`Role of ${user.email}`}
                    value={user.role}
                    onChange={(event) => void update(user, { role: event.target.value as Role })}
                  >
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td>
                  <Badge tone={user.is_active ? "green" : "red"}>{user.is_active ? "Active" : "Disabled"}</Badge>
                </td>
                <td>{formatDateTime(user.created_at)}</td>
                <td className="right actions">
                  <Button onClick={() => setKeyFor(user)} disabled={!user.is_active}>
                    Issue key
                  </Button>
                  {user.is_active ? (
                    <ConfirmButton confirmLabel="Disable user" onConfirm={() => update(user, { is_active: false })}>
                      Disable
                    </ConfirmButton>
                  ) : (
                    <Button onClick={() => void update(user, { is_active: true })}>Enable</Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <h2 className="section-title">All API keys</h2>
      {keys.data && keys.data.length === 0 ? <p className="muted">No keys have been issued yet.</p> : null}
      {keys.data && keys.data.length > 0 ? (
        <table className="table" aria-label="All API keys">
          <thead>
            <tr>
              <th>Owner</th>
              <th>Name</th>
              <th>Key ID</th>
              <th>Created</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.data.map((key) => (
              <tr key={key.key_id}>
                <td>{key.user_email ?? `user ${key.user_id}`}</td>
                <td>{key.name}</td>
                <td>
                  <code>gwk_{key.key_id.slice(0, 8)}…</code>
                </td>
                <td>{formatDateTime(key.created_at)}</td>
                <td>
                  <KeyStatusBadge apiKey={key} />
                </td>
                <td className="right">
                  {keyStatus(key) === "active" ? (
                    <ConfirmButton confirmLabel="Revoke key" onConfirm={() => revoke(key.key_id)}>
                      Revoke
                    </ConfirmButton>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {inviting ? (
        <InviteModal
          onClose={() => setInviting(false)}
          onCreated={async () => {
            setInviting(false);
            await users.reload();
          }}
        />
      ) : null}
      {keyFor ? (
        <CreateKeyModal
          title={`Issue a key for ${keyFor.email}`}
          create={(name, days) => api.createKeyFor(keyFor.id, name, days)}
          onClose={() => setKeyFor(null)}
          onCreated={async (key) => {
            setKeyFor(null);
            setIssued(key);
            await keys.reload();
          }}
        />
      ) : null}
      {issued ? <KeyRevealModal apiKey={issued.key} name={issued.name} gatewayUrl={gatewayUrl()} onClose={() => setIssued(null)} /> : null}
    </>
  );
}

function InviteModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createUser(email.trim(), password, role);
      await onCreated();
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <Modal title="Invite a team member" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <TextInput label="Email" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        <TextInput
          label="Initial password"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          hint="At least 10 characters. Share it with them privately; they can sign in right away."
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <Select label="Role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
          <option value="member">Member: uses tools and chat</option>
          <option value="admin">Admin: also manages servers and the team</option>
        </Select>
        <ErrorNote message={error} />
        <div className="modal-actions">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={busy}>
            Create account
          </Button>
        </div>
      </form>
    </Modal>
  );
}
