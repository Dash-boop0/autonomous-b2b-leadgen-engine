"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { User } from "@/lib/types";
import { Card, ErrorBox } from "@/components/ui";

export default function LoginPage() {
  const { login, user } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("demo");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<User[]>("/api/auth/demo-users")
      .then(setUsers)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => {
    if (user) router.replace("/");
  }, [user, router]);

  return (
    <div className="login">
      <Card title="Sign in (demo)">
        <p className="muted small">
          Demo authentication only: four seeded users share the password <code>demo</code>. Roles: operator runs workflows, reviewer approves,
          admin controls the emergency stop and resets, viewer reads.
        </p>
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError(null);
            try {
              await login(username, password);
              router.replace("/");
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="field">
            User
            <select value={username} onChange={(e) => setUsername(e.target.value)} name="username">
              {(users.length ? users : [{ id: "x", username: "operator", display_name: "Operator", role: "operator" } as User]).map((u) => (
                <option key={u.id} value={u.username}>
                  {u.display_name} ({u.role})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} name="password" autoComplete="current-password" />
          </label>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          {error && <ErrorBox error={error} />}
        </form>
      </Card>
    </div>
  );
}
