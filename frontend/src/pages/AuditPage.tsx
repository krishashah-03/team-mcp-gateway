import { useState } from "react";
import { api } from "../lib/api";
import { formatDateTime, formatDuration } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { Badge, Button, Empty, ErrorNote, Loading, PageHeader, Select, TextInput } from "../components/ui";

const PAGE_SIZE = 25;

export default function AuditPage() {
  const users = useLoad(() => api.users());
  const [userId, setUserId] = useState("");
  const [server, setServer] = useState("");
  const [outcome, setOutcome] = useState("");
  const [offset, setOffset] = useState(0);

  const page = useLoad(
    () =>
      api.audit({
        user_id: userId ? Number(userId) : undefined,
        server: server.trim() || undefined,
        outcome: outcome || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    [userId, server, outcome, offset],
  );

  const total = page.data?.total ?? 0;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + PAGE_SIZE, total);

  function filter<T>(setter: (value: T) => void) {
    return (value: T) => {
      setOffset(0);
      setter(value);
    };
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Every tool call: who made it, which server and tool, when, how long, and whether it worked."
        actions={<Button onClick={() => void page.reload()}>Refresh</Button>}
      />

      <div className="filters">
        <Select label="User" value={userId} onChange={(event) => filter(setUserId)(event.target.value)}>
          <option value="">Everyone</option>
          {users.data?.map((user) => (
            <option key={user.id} value={user.id}>
              {user.email}
            </option>
          ))}
        </Select>
        <TextInput
          label="Server"
          placeholder="slug, e.g. woocommerce"
          value={server}
          onChange={(event) => filter(setServer)(event.target.value)}
        />
        <Select label="Outcome" value={outcome} onChange={(event) => filter(setOutcome)(event.target.value)}>
          <option value="">Any</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
        </Select>
      </div>

      <ErrorNote message={page.error} />
      {page.loading && !page.data ? <Loading /> : null}
      {page.data && page.data.items.length === 0 ? (
        <Empty title="No tool calls match">Calls appear here as soon as someone uses a tool.</Empty>
      ) : null}

      {page.data && page.data.items.length > 0 ? (
        <>
          <div className="table-scroll">
            <table className="table" aria-label="Audit log">
              <thead>
                <tr>
                  <th>When</th>
                  <th>User</th>
                  <th>Via</th>
                  <th>Server</th>
                  <th>Tool</th>
                  <th className="right">Duration</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((event) => (
                  <tr key={event.id}>
                    <td>{formatDateTime(event.occurred_at)}</td>
                    <td>{event.actor_email ?? <span className="muted">deleted user</span>}</td>
                    <td>
                      <Badge tone={event.channel === "chat" ? "blue" : "gray"}>{event.channel}</Badge>
                    </td>
                    <td>{event.server ?? "-"}</td>
                    <td>
                      <code>{event.tool ?? "-"}</code>
                    </td>
                    <td className="right">{formatDuration(event.duration_ms)}</td>
                    <td>
                      {event.outcome === "success" ? (
                        <Badge tone="green">success</Badge>
                      ) : (
                        <Badge tone="red">{event.error_category ?? "failure"}</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="muted">
              {first}-{last} of {total}
            </span>
            <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </Button>
            <Button disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </Button>
          </div>
        </>
      ) : null}
    </>
  );
}
