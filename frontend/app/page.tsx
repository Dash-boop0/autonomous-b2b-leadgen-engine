"use client";

import Link from "next/link";
import { useApi } from "@/lib/useApi";
import type { Approval, Dashboard } from "@/lib/types";
import { Card, Empty, ErrorBox, Kpi, Loading, PageHeader, StatusBadge } from "@/components/ui";
import { humanize, shortDate } from "@/lib/format";

export default function Overview() {
  const { data, error, loading, reload } = useApi<Dashboard>("/api/dashboard");
  const pending = useApi<Approval[]>("/api/approvals?status=pending");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;
  const c = data.counts;
  return (
    <>
      <PageHeader
        title="Overview"
        lead="Acquisition engine for a fictional seller (Northwind Analytics). Agents research, discover, verify, score and draft; humans approve anything consequential."
      />
      <div className="kpis">
        <Kpi label="Accounts" value={c.accounts} />
        <Kpi label="Contacts" value={c.contacts} sub={`${c.leads} leads · ${c.qualified_leads} qualified, not yet in a campaign`} />
        <Kpi label="Pending approvals" value={c.pending_approvals} />
        <Kpi label="Dry-run sends" value={c.sent_simulated} sub="simulated, never delivered" />
        <Kpi label="Replies to review" value={c.replies_needing_review} />
        <Kpi label="Open errors" value={c.open_errors} />
      </div>
      <div className="grid grid-2">
        <Card title="Guided demo">
          <ol className="checklist">
            {data.checklist.map((i) => (
              <li key={i.key}>
                <span className={`tick${i.done ? " done" : ""}`} aria-hidden>
                  {i.done ? "✓" : ""}
                </span>
                <Link href={i.href}>{i.label}</Link>
                <span className="sr-only">{i.done ? "(done)" : "(to do)"}</span>
              </li>
            ))}
          </ol>
          <p className="muted small" style={{ marginTop: 10 }}>
            Tip: use the user switcher in the top bar. The operator starts workflows; a different person (the reviewer) must approve them.
          </p>
        </Card>
        <Card title="Waiting for a human" actions={<Link href="/approvals">All approvals</Link>}>
          {pending.error ? (
            <ErrorBox error={pending.error} />
          ) : !pending.data ? (
            <Loading />
          ) : pending.data.length === 0 ? (
            <Empty>No pending approvals.</Empty>
          ) : (
            <ul className="steps">
              {pending.data.map((a) => (
                <li key={a.id}>
                  <StatusBadge status={a.status} /> <strong>{humanize(a.action)}</strong>
                  <span className="muted small">{a.summary}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Active ICP">
          {data.active_icp ? (
            <p>
              <Link href="/icp">{data.active_icp.name}</Link>
            </p>
          ) : (
            <Empty>
              No approved ICP yet. A draft from market research is waiting in <Link href="/approvals">Approvals</Link>.
            </Empty>
          )}
          {data.latest_campaign && (
            <p className="small">
              Latest campaign: <Link href="/campaigns">{data.latest_campaign.name}</Link> <StatusBadge status={data.latest_campaign.status} />
            </p>
          )}
        </Card>
        <Card title="Recent workflow runs" actions={<Link href="/workflows">All runs</Link>}>
          {data.recent_runs.length === 0 ? (
            <Empty>No runs yet.</Empty>
          ) : (
            <ul className="steps">
              {data.recent_runs.map((r) => (
                <li key={r.id}>
                  <StatusBadge status={r.status} /> {r.title} <span className="muted small">{shortDate(r.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
