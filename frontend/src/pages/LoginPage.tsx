import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { ApiError } from "../lib/api";
import { Button, ErrorNote, InfoNote, TextInput } from "../components/ui";

export default function LoginPage() {
  const { login, notice } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 429) {
        const wait = caught.retryAfter ? ` Try again in about ${Math.ceil(caught.retryAfter / 60)} min.` : "";
        setError(`Too many failed attempts.${wait}`);
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not sign in");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit} aria-labelledby="login-title">
        <div className="brand brand-large">
          <span className="brand-mark" aria-hidden="true" />
          <span id="login-title">Team MCP Gateway</span>
        </div>
        <p className="muted">Sign in to manage servers or use your team's tools.</p>
        {notice ? <InfoNote>{notice}</InfoNote> : null}
        <TextInput
          label="Email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <TextInput
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <ErrorNote message={error} />
        <Button type="submit" variant="primary" busy={busy}>
          Sign in
        </Button>
      </form>
    </div>
  );
}
