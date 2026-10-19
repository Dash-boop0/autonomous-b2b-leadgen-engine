"use client";

import { useState } from "react";
import { api, driveRun, startAndDrive } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { ErrorItem, Run, WorkflowDefinition } from "@/lib/types";
import { ActionButton, Badge, Card, Disclosure, Empty, ErrorBox, Json, Loading, PageHeader, StatusBadge } from "@/components/ui";
import { humanize, isTerminalRun, shortDate } from "@/lib/format";

function RunDetail({ run, onChange }: { run: Run; onChange: () => void }) {
  const { can } = useAuth();
  const act = async (path: string) => {
    await api.post(`/api/runs/${run.id}/${path}`, path === "cancel" ? { reason: "cancelled from UI" } : {});
    onChange();
  };
  return (
    <Card
      title={run.title}
      actions={
        <>
          <StatusBadge status={run.status} />
          {can("workflows.run") && !isTerminalRun(run.status) && (
            <>
              <ActionButton size="sm" onClick={async () => { await driveRun(run.id); onChange(); }}>
                Advance
              </ActionButton>
              {run.status === "paused" ? (
                <ActionButton size="sm" onClick={() => act("resume")}>Resume</ActionButton>
              ) : (
                <ActionButton size="sm" onClick={() => act("pause")}>Pause</ActionButton>
              )}
              <ActionButton size="sm" variant="danger" confirm="Cancel this run?" onClick={() => act("cancel")}>
                Cancel
              </ActionButton>
            </>
          )}
        </>
      }
    >
      <p className="small muted">
        <code>{run.id}</code> · started by {run.created_by} · {shortDate(run.created_at)}
      </p>
      {run.error && <ErrorBox error={run.error} />}
      <ol className="steps" style={{ marginTop: 8 }}>
        {run.steps.map((s) => (
          <li key={s.id}>
            <StatusBadge status={s.status} />
            <strong>{s.name}</strong>
            {s.agent ? <Badge tone="accent">agent: {s.agent}</Badge> : s.name.startsWith("approve") ? <Badge tone="warn">human gate</Badge> : <Badge>deterministic</Badge>}
            <span className="muted small">
              attempts {s.attempts}/{s.max_attempts}
              {s.next_attempt_at && s.status === "retry_wait" ? ` · retry at ${shortDate(s.next_attempt_at)}` : ""}
            </span>
            {s.error && <span className="small" style={{ color: "var(--bad)" }}>{s.error}</span>}
            {Object.keys(s.output ?? {}).length > 0 && (
              <Disclosure summary="output">
                <Json value={s.output} />
              </Disclosure>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

export default function WorkflowsPage() {
  const { can } = useAuth();
  const defs = useApi<WorkflowDefinition[]>("/api/workflows");
  const runs = useApi<Run[]>("/api/runs?limit=60");
  const errors = useApi<ErrorItem[]>("/api/errors");
  const [sel, setSel] = useState<string | null>(null);
  const selected = runs.data?.find((r) => r.id === (sel ?? runs.data?.[0]?.id));
  const reload = () => {
    void runs.reload();
    void errors.reload();
  };
  return (
    <>
      <PageHeader
        title="Workflows"
        lead="Durable, database-backed orchestration: explicit step states, idempotency keys, retries with exponential backoff, an error queue, pause/resume and a global emergency stop. Runs advance through request-driven ticks (serverless-friendly)."
        actions={
          can("workflows.run") && (
            <ActionButton onClick={async () => { await startAndDrive("/api/workflows/governance_sweep/runs", { input: {} }); reload(); }}>
              Run governance sweep
            </ActionButton>
          )
        }
      />
      <div className="split">
        <div className="stack">
          <Card title="Runs">
            {runs.error ? (
              <ErrorBox error={runs.error} onRetry={runs.reload} />
            ) : !runs.data ? (
              <Loading />
            ) : runs.data.length === 0 ? (
              <Empty>No runs yet.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Workflow</th>
                      <th>Status</th>
                      <th>Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.map((r) => (
                      <tr key={r.id} className={`clickable${selected?.id === r.id ? " is-selected" : ""}`} onClick={() => setSel(r.id)}>
                        <td>
                          <button className="linkbtn" onClick={() => setSel(r.id)}>
                            {r.title}
                          </button>
                        </td>
                        <td>
                          <StatusBadge status={r.status} />
                        </td>
                        <td className="small">
                          {shortDate(r.created_at)}
                          <div className="muted">{r.created_by}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Card title={`Error queue (${(errors.data ?? []).filter((e) => e.status === "open").length} open)`}>
            {!errors.data ? (
              <Loading />
            ) : errors.data.length === 0 ? (
              <Empty>No failed steps.</Empty>
            ) : (
              <ul className="steps">
                {errors.data.map((e) => (
                  <li key={e.id}>
                    <StatusBadge status={e.status === "open" ? "failed" : e.status} />
                    <strong>
                      {e.workflow}.{e.step_name}
                    </strong>
                    <span className="small wrap">{e.error.split("\n")[0]}</span>
                    {e.status === "open" && can("workflows.run") && (
                      <>
                        <ActionButton size="sm" onClick={async () => { await api.post(`/api/errors/${e.id}/retry`); reload(); }}>
                          Retry
                        </ActionButton>
                        <ActionButton size="sm" onClick={async () => { await api.post(`/api/errors/${e.id}/dismiss`); reload(); }}>
                          Dismiss
                        </ActionButton>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        <div className="stack">
          {selected ? <RunDetail run={selected} onChange={reload} /> : <Empty>Select a run.</Empty>}
          <Card title="Definitions">
            {!defs.data ? (
              <Loading />
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {defs.data.map((d) => (
                  <Disclosure key={d.name} summary={`${d.title} — ${d.steps.length} steps`}>
                    <p className="small muted">{d.description}</p>
                    <ol className="small">
                      {d.steps.map((s) => (
                        <li key={s.name}>
                          {s.name} <span className="muted">({s.agent ? `agent ${s.agent}` : humanize(s.kind)})</span>
                        </li>
                      ))}
                    </ol>
                  </Disclosure>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
