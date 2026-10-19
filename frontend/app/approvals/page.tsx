"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Approval } from "@/lib/types";
import { ActionButton, Badge, Card, Empty, ErrorBox, Loading, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { humanize, shortDate } from "@/lib/format";

function subjectLink(a: Approval) {
  if (a.subject_type === "campaign") return <Link href="/campaigns">{a.subject_name ?? a.subject_id}</Link>;
  if (a.subject_type === "icp_version") return <Link href="/icp">{a.subject_name ?? a.subject_id}</Link>;
  return a.subject_id;
}

function Decide({ a, onDone }: { a: Approval; onDone: () => void }) {
  const { can, refreshSystem } = useAuth();
  const [reason, setReason] = useState("");
  if (!can("approvals.decide")) return <span className="muted small">Your role cannot decide approvals. Switch to the reviewer.</span>;
  const decide = async (decision: "approved" | "rejected") => {
    await api.post(`/api/approvals/${a.id}/decide`, { decision, reason: reason || null });
    await refreshSystem();
    onDone();
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <label className="field">
        Reason (required to reject)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} aria-label={`Reason for ${a.action}`} />
      </label>
      <div className="row">
        <ActionButton variant="primary" onClick={() => decide("approved")}>
          Approve
        </ActionButton>
        <ActionButton variant="danger" onClick={() => decide("rejected")}>
          Reject
        </ActionButton>
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const { data, error, loading, reload } = useApi<Approval[]>("/api/approvals");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const pending = (data ?? []).filter((a) => a.status === "pending");
  const decided = (data ?? []).filter((a) => a.status !== "pending");
  return (
    <>
      <PageHeader
        title="Approvals"
        lead="The approval engine is deterministic code: agents can request approvals but never decide them, nobody can approve their own request, and four-eyes policies stop the person who started a workflow from approving it."
      />
      <div className="stack">
        {pending.length === 0 ? (
          <Empty>Nothing is waiting for approval.</Empty>
        ) : (
          pending.map((a) => (
            <Card
              key={a.id}
              title={
                <span className="row">
                  <StatusBadge status={a.status} /> {humanize(a.action)}
                </span>
              }
            >
              <div className="grid grid-2">
                <div className="stack" style={{ gap: 6 }}>
                  <div>Subject: {subjectLink(a)}</div>
                  <div className="small">{a.summary}</div>
                  <div className="small muted">
                    Requested by <Badge tone={a.requested_by.actor_type === "agent" ? "accent" : "neutral"}>{a.requested_by.actor_type}</Badge>{" "}
                    {a.requested_by.actor_id} · started by {a.initiated_by ?? "-"} · policy <code>{a.policy_id}</code> · {shortDate(a.created_at)}
                  </div>
                  {a.subject_type === "campaign" && (
                    <Notice tone="accent">
                      Review the drafts first on the <Link href="/campaigns">Campaigns</Link> page; you can exclude individual drafts before
                      approving. Approval only queues dry-run messages.
                    </Notice>
                  )}
                </div>
                <Decide a={a} onDone={reload} />
              </div>
            </Card>
          ))
        )}
        <Card title="Decided">
          {decided.length === 0 ? (
            <Empty>No decisions yet.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Subject</th>
                    <th>Status</th>
                    <th>Requested by</th>
                    <th>Decided by</th>
                    <th>Reason</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {decided.map((a) => (
                    <tr key={a.id}>
                      <td>{humanize(a.action)}</td>
                      <td>{subjectLink(a)}</td>
                      <td>
                        <StatusBadge status={a.status} />
                      </td>
                      <td>
                        {a.requested_by.actor_type}:{a.requested_by.actor_id}
                      </td>
                      <td>{a.decided_by}</td>
                      <td className="wrap">{a.reason}</td>
                      <td>{shortDate(a.decided_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
