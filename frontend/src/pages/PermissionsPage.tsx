import { useState, type FormEvent } from "react";
import { api, type PermissionRule, type Role } from "../lib/api";
import { messageOf, useLoad } from "../lib/useLoad";
import {
  Badge,
  Button,
  ConfirmButton,
  Empty,
  ErrorNote,
  InfoNote,
  Loading,
  PageHeader,
  Select,
} from "../components/ui";

export function describeRule(rule: PermissionRule, emails: Map<number, string>): string {
  const who = rule.role ? `${rule.role}s` : (emails.get(rule.user_id ?? -1) ?? `user ${rule.user_id}`);
  const what = rule.tool_name ? `${rule.server_slug}__${rule.tool_name}` : `every tool on ${rule.server_slug}`;
  return `${who} ${rule.effect === "deny" ? "cannot use" : "can use"} ${what}`;
}

export default function PermissionsPage() {
  const rules = useLoad(() => api.permissions());
  const users = useLoad(() => api.users());
  const servers = useLoad(() => api.servers());
  const [error, setError] = useState<string | null>(null);

  const emails = new Map((users.data ?? []).map((user) => [user.id, user.email] as const));

  async function remove(rule: PermissionRule) {
    setError(null);
    try {
      await api.deletePermission(rule.id);
      await rules.reload();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  return (
    <>
      <PageHeader
        title="Permissions"
        subtitle="Control who can use which tools. Tools a user may not use are hidden from them and rejected if called directly."
      />
      <InfoNote>
        With no rule, a tool is allowed. The most specific rule wins: a rule for a person beats a rule for a role, and a
        rule for one tool beats a rule for a whole server. If two rules are equally specific, deny wins.
      </InfoNote>
      <ErrorNote message={rules.error ?? error} />

      <AddRule
        users={users.data ?? []}
        servers={servers.data ?? []}
        serversLoading={servers.loading && !servers.data}
        onAdded={() => rules.reload()}
      />

      <h2 className="section-title">Current rules</h2>
      {rules.loading && !rules.data ? <Loading /> : null}
      {rules.data && rules.data.length === 0 ? <Empty title="No rules">Everyone can use every tool.</Empty> : null}
      {rules.data && rules.data.length > 0 ? (
        <table className="table" aria-label="Permission rules">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Effect</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.data.map((rule) => (
              <tr key={rule.id}>
                <td>{describeRule(rule, emails)}</td>
                <td>
                  <Badge tone={rule.effect === "deny" ? "red" : "green"}>{rule.effect}</Badge>
                </td>
                <td className="right">
                  <ConfirmButton confirmLabel="Delete rule" onConfirm={() => remove(rule)}>
                    Delete
                  </ConfirmButton>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </>
  );
}

function AddRule({
  users,
  servers,
  serversLoading,
  onAdded,
}: {
  users: { id: number; email: string }[];
  servers: { slug: string; name: string; tools: { name: string }[] }[];
  serversLoading: boolean;
  onAdded: () => Promise<void>;
}) {
  const [subject, setSubject] = useState<"role" | "user">("role");
  const [role, setRole] = useState<Role>("member");
  const [userId, setUserId] = useState("");
  const [slug, setSlug] = useState("");
  const [tool, setTool] = useState("");
  const [effect, setEffect] = useState<"deny" | "allow">("deny");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = servers.find((server) => server.slug === slug);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.setPermission({
        ...(subject === "role" ? { role } : { user_id: Number(userId) }),
        server_slug: slug,
        tool_name: tool || null,
        effect,
      });
      await onAdded();
      setTool("");
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel form inline-form" onSubmit={submit} aria-label="Add a rule">
      <h2>Add a rule</h2>
      <div className="grid-4">
        <Select label="Applies to" value={subject} onChange={(event) => setSubject(event.target.value as "role" | "user")}>
          <option value="role">A role</option>
          <option value="user">One person</option>
        </Select>
        {subject === "role" ? (
          <Select label="Role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
            <option value="member">Members</option>
            <option value="admin">Admins</option>
          </Select>
        ) : (
          <Select label="Person" required value={userId} onChange={(event) => setUserId(event.target.value)}>
            <option value="">Choose…</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.email}
              </option>
            ))}
          </Select>
        )}
        <Select
          label="Server"
          required
          value={slug}
          onChange={(event) => {
            setSlug(event.target.value);
            setTool("");
          }}
        >
          <option value="">{serversLoading ? "Loading…" : "Choose…"}</option>
          {servers.map((server) => (
            <option key={server.slug} value={server.slug}>
              {server.name}
            </option>
          ))}
        </Select>
        <Select label="Tool" value={tool} disabled={!slug} onChange={(event) => setTool(event.target.value)}>
          <option value="">All tools on this server</option>
          {selected?.tools.map((entry) => (
            <option key={entry.name} value={entry.name}>
              {entry.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid-4">
        <Select label="Effect" value={effect} onChange={(event) => setEffect(event.target.value as "deny" | "allow")}>
          <option value="deny">Deny</option>
          <option value="allow">Allow</option>
        </Select>
        <div className="field align-end">
          <Button type="submit" variant="primary" busy={busy} disabled={!slug || (subject === "user" && !userId)}>
            Save rule
          </Button>
        </div>
      </div>
      <ErrorNote message={error} />
    </form>
  );
}
