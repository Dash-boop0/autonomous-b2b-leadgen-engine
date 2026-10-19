"use client";

import { useState } from "react";
import { useApi } from "@/lib/useApi";
import type { AuditEvent } from "@/lib/types";
import { Badge, Disclosure, Empty, ErrorBox, Json, Loading, PageHeader } from "@/components/ui";
import { shortDate } from "@/lib/format";

const PAGE = 50;

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [actorType, setActorType] = useState("");
  const [severity, setSeverity] = useState("");
  const [offset, setOffset] = useState(0);
  const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (action) p.set("action", action);
  if (actorType) p.set("actor_type", actorType);
  if (severity) p.set("severity", severity);
  const { data, error, loading, reload } = useApi<{ total: number; items: AuditEvent[] }>(`/api/audit?${p}`);
  return (
    <>
      <PageHeader
        title="Audit log"
        lead="Append-only record of every agent action, tool denial, approval, state change, send, suppression and error: who, what, when, before/after and evidence ids."
      />
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field">
          Action contains
          <input value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} placeholder="e.g. approval, send, agent." />
        </label>
        <label className="field">
          Actor
          <select value={actorType} onChange={(e) => { setActorType(e.target.value); setOffset(0); }}>
            <option value="">All</option>
            <option value="human">human</option>
            <option value="agent">agent</option>
            <option value="system">system</option>
          </select>
        </label>
        <label className="field">
          Severity
          <select value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }}>
            <option value="">All</option>
            <option value="info">info</option>
            <option value="warn">warn</option>
            <option value="error">error</option>
          </select>
        </label>
      </div>
      {loading && !data ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} onRetry={reload} />
      ) : !data || data.items.length === 0 ? (
        <Empty>No matching events.</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Subject</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id}>
                    <td className="small">{shortDate(e.at)}</td>
                    <td className="small">
                      <Badge tone={e.actor_type === "agent" ? "accent" : e.actor_type === "human" ? "good" : "neutral"}>{e.actor_type}</Badge>
                      <div className="wrap">{e.actor_id}</div>
                    </td>
                    <td className="small">
                      <code>{e.action}</code> {e.severity !== "info" && <Badge tone={e.severity === "error" ? "bad" : "warn"}>{e.severity}</Badge>}
                    </td>
                    <td className="small wrap">
                      {e.subject_type}:{e.subject_id}
                      {e.run_id && <div className="muted">run {e.run_id}</div>}
                    </td>
                    <td className="small">
                      {e.evidence_ids.length > 0 && <span className="muted">{e.evidence_ids.length} evidence · </span>}
                      {(e.before !== null || e.after !== null) && (
                        <Disclosure summary="before / after">
                          <Json value={{ before: e.before, after: e.after }} />
                        </Disclosure>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Newer
            </button>
            <span className="small muted">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
            </span>
            <button className="btn btn-sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
              Older
            </button>
          </div>
        </>
      )}
    </>
  );
}
