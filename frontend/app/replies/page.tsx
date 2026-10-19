"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Reply } from "@/lib/types";
import { ActionButton, Badge, Card, Empty, ErrorBox, Loading, MockTag, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { humanize, shortDate } from "@/lib/format";

const LABELS = [
  "interested",
  "not_interested",
  "unsubscribe",
  "out_of_office",
  "referral",
  "question_product",
  "question_pricing",
  "question_legal",
  "unknown",
];

function Triage({ r, onDone }: { r: Reply; onDone: () => void }) {
  const [label, setLabel] = useState(r.classification);
  return (
    <div className="row">
      <label className="field">
        Reclassify
        <select value={label} onChange={(e) => setLabel(e.target.value)} aria-label={`Classification for reply from ${r.contact_name}`}>
          {LABELS.map((l) => (
            <option key={l} value={l}>
              {humanize(l)}
            </option>
          ))}
        </select>
      </label>
      <ActionButton
        size="sm"
        variant="primary"
        onClick={async () => {
          await api.post(`/api/replies/${r.id}/triage`, { classification: label !== r.classification ? label : null, status: "resolved" });
          onDone();
        }}
      >
        Mark resolved
      </ActionButton>
    </div>
  );
}

export default function RepliesPage() {
  const { can } = useAuth();
  const [filter, setFilter] = useState("");
  const { data, error, loading, reload } = useApi<Reply[]>(`/api/replies${filter ? `?status=${filter}` : ""}`);
  return (
    <>
      <PageHeader
        title="Reply triage"
        lead="Simulated inbound replies classified by the (mock) LLM. Low-confidence replies go to a human; pricing and legal questions always escalate; product answers cite approved passages or abstain. Nothing is sent automatically."
      />
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field">
          Status
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All</option>
            <option value="needs_review">Needs review</option>
            <option value="escalated">Escalated</option>
            <option value="auto_handled">Auto-handled</option>
            <option value="resolved">Resolved</option>
          </select>
        </label>
      </div>
      {loading && !data ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} onRetry={reload} />
      ) : !data || data.length === 0 ? (
        <Empty>
          No replies yet. Approve a campaign and run the send + inbox cycle on <Link href="/campaigns">Campaigns</Link>.
        </Empty>
      ) : (
        <div className="stack">
          {data.map((r) => (
            <Card
              key={r.id}
              title={
                <span className="row">
                  {r.contact_name} <span className="muted small">{r.account_name}</span>
                </span>
              }
              actions={
                <>
                  <Badge tone="accent">{humanize(r.classification)}</Badge>
                  <Badge tone={r.confidence < 0.6 ? "warn" : "neutral"}>conf {r.confidence.toFixed(2)}</Badge>
                  {r.classifier === "mock-llm" && <MockTag label="mock LLM" />}
                  <StatusBadge status={r.status} />
                </>
              }
            >
              <div className="grid grid-2">
                <div>
                  <div className="small muted">
                    Simulated reply · {shortDate(r.created_at)} {r.suppressed && <Badge tone="bad">now suppressed</Badge>}
                  </div>
                  <div className="email">{r.body}</div>
                </div>
                <div className="stack" style={{ gap: 8 }}>
                  {r.response_status === "drafted" && (
                    <>
                      <div className="small">
                        <strong>Suggested response</strong> (draft only, needs a human):
                      </div>
                      <div className="email">{r.suggested_response}</div>
                      <div className="small">
                        Cites:{" "}
                        {r.response_citations.map((c) => (
                          <code key={c.id} style={{ marginRight: 6 }}>
                            {c.id} v{c.version}
                          </code>
                        ))}
                      </div>
                    </>
                  )}
                  {(r.response_status === "escalated" || r.response_status === "abstained") && (
                    <Notice tone="warn">
                      <strong>{humanize(r.response_status)}:</strong> {r.escalation_reason}
                    </Notice>
                  )}
                  {r.response_status === "none" &&
                    (r.needs_human ? (
                      <Notice tone="warn">
                        <strong>Needs a human:</strong> low-confidence classification ({r.confidence.toFixed(2)}); no reply is drafted.
                      </Notice>
                    ) : (
                      <p className="small muted">No response needed ({humanize(r.classification)}).</p>
                    ))}
                  {can("replies.triage") && r.status !== "resolved" && <Triage r={r} onDone={reload} />}
                  {r.triaged_by && <p className="small muted">Triaged by {r.triaged_by}</p>}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
