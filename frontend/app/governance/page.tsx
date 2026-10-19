"use client";

import { useState } from "react";
import { API_URL, api, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Suppression } from "@/lib/types";
import { ActionButton, Badge, Card, Empty, ErrorBox, Loading, MockTag, Notice, PageHeader } from "@/components/ui";
import { shortDate } from "@/lib/format";

export default function GovernancePage() {
  const { can, system, refreshSystem } = useAuth();
  const sup = useApi<Suppression[]>("/api/suppressions");
  const crm = useApi<{ id: string; entity_id: string; operation: string; payload: Record<string, unknown>; created_at: string }[]>("/api/crm/outbox");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("opt_out");
  const [profile, setProfile] = useState("basic");
  return (
    <>
      <PageHeader
        title="Governance"
        lead="Emergency stop, suppression list, simulated clock, demo reset and the mock CRM outbox. Suppression is checked deterministically before anything is queued and again at send time."
      />
      <div className="grid grid-2">
        <Card title="Emergency stop">
          {system ? (
            <>
              <p>
                Status: {system.emergency_stop ? <Badge tone="bad">STOPPED</Badge> : <Badge tone="good">running</Badge>}{" "}
                {system.stop_reason && <span className="small muted">({system.stop_reason})</span>}
              </p>
              <p className="small muted">
                When on, workflow ticks halt and no message can be queued or dispatched. Use the STOP button in the top bar (admins only).
              </p>
            </>
          ) : (
            <Loading />
          )}
          <h3 style={{ marginTop: 12 }}>Providers</h3>
          <ul className="small">
            {system &&
              Object.entries(system.providers).map(([k, p]) => (
                <li key={k}>
                  {k}: <code>{p.name}</code> {p.mock && <MockTag />}
                </li>
              ))}
          </ul>
          <p className="small">
            Send mode: <Badge tone="warn">dry-run</Badge> (no real sender is implemented). Simulated clock offset: +{system?.sim_clock_offset_days ?? 0} days.
          </p>
        </Card>
        <Card title="Reset demo (admin)">
          <p className="small muted">Wipes all data and re-seeds the synthetic dataset. The reset itself is recorded in the audit log.</p>
          <div className="row">
            <select value={profile} onChange={(e) => setProfile(e.target.value)} aria-label="Seed profile">
              <option value="basic">basic: start of the demo (ICP waiting for approval)</option>
              <option value="full">full: whole pipeline pre-run</option>
            </select>
            <ActionButton
              variant="danger"
              disabled={!can("system.reset")}
              title={can("system.reset") ? undefined : "Admin role required"}
              confirm="Reset all demo data?"
              onClick={async () => {
                const r = await api.post<{ token: string | null }>("/api/admin/reset-demo", { profile });
                if (r.token) setToken(r.token);
                await refreshSystem();
                window.location.href = "/";
              }}
            >
              Reset demo
            </ActionButton>
          </div>
        </Card>
        <Card title="Suppression list">
          {can("suppressions.manage") && (
            <form
              className="row"
              style={{ marginBottom: 10 }}
              onSubmit={(e) => e.preventDefault()}
              aria-label="Add suppression"
            >
              <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="email or domain" aria-label="Email or domain" />
              <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason">
                {["opt_out", "bounce", "complaint", "manual", "legal"].map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
              <ActionButton
                disabled={value.length < 3}
                onClick={async () => {
                  await api.post("/api/suppressions", { email_or_domain: value, reason, note: "added in UI" });
                  setValue("");
                  await sup.reload();
                }}
              >
                Add
              </ActionButton>
            </form>
          )}
          {sup.error ? (
            <ErrorBox error={sup.error} />
          ) : !sup.data ? (
            <Loading />
          ) : sup.data.length === 0 ? (
            <Empty>Empty.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Email / domain</th>
                    <th>Reason</th>
                    <th>Added</th>
                  </tr>
                </thead>
                <tbody>
                  {sup.data.map((s) => (
                    <tr key={s.id}>
                      <td className="mono small wrap">{s.email_or_domain}</td>
                      <td>
                        <Badge tone="bad">{s.reason}</Badge>
                        <div className="small muted">{s.note}</div>
                      </td>
                      <td className="small">
                        {shortDate(s.created_at)}
                        <div className="muted">{s.created_by}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Mock CRM outbox" actions={<MockTag label="mock CRM" />}>
          {!crm.data ? (
            <Loading />
          ) : crm.data.length === 0 ? (
            <Empty>No CRM updates yet.</Empty>
          ) : (
            <ul className="small">
              {crm.data.slice(0, 30).map((x) => (
                <li key={x.id}>
                  {x.operation} lead <code>{x.entity_id}</code> → <strong>{String(x.payload.stage)}</strong> <span className="muted">{shortDate(x.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
          <Notice>
            Shared-interface export for the revenue-ops repo: <a href={`${API_URL}/api/export/shared`}>/api/export/shared</a> (requires sign-in token; use the API
            docs at <a href={`${API_URL}/docs`}>/docs</a>).
          </Notice>
        </Card>
      </div>
    </>
  );
}
