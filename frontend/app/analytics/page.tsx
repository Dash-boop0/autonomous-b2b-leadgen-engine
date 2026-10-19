"use client";

import { useState } from "react";
import Link from "next/link";
import { startAndDrive } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Analytics, Campaign, MetricsBlock } from "@/lib/types";
import { ActionButton, Badge, Card, Empty, ErrorBox, Kpi, Loading, MockTag, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { humanize, pct, ratioText } from "@/lib/format";

const RATES = ["delivery_rate", "bounce_rate", "reply_rate", "positive_reply_rate", "unsubscribe_rate"];

function SegmentTable({ rows }: { rows: (MetricsBlock & { segment: string })[] }) {
  if (rows.length === 0) return <Empty>No data.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th className="num">Contacts sent</th>
            {RATES.map((r) => (
              <th key={r} className="num">
                {humanize(r)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.segment}>
              <td>{s.segment}</td>
              <td className="num">{s.counts.contacts_sent}</td>
              {RATES.map((r) => (
                <td key={r} className="num" title={s.rates[r].formula}>
                  {pct(s.rates[r])} <span className="muted small">({ratioText(s.rates[r])})</span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function View({ id }: { id: string }) {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<Analytics>(`/api/analytics/campaigns/${id}`);
  const [dim, setDim] = useState("industry");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;
  const run = data.recommendation_run;
  return (
    <div className="stack">
      <Notice tone="warn">
        <strong>Simulated outcomes, not real results.</strong> Events come from a deterministic dry-run simulator over synthetic contacts. Small
        samples are directional only.
      </Notice>
      <div className="kpis">
        <Kpi label="Contacts sent" value={data.counts.contacts_sent} sub={`${data.counts.messages_sent} messages`} />
        {RATES.map((r) => (
          <Kpi key={r} label={humanize(r)} value={pct(data.rates[r], 1)} sub={`${ratioText(data.rates[r])} · ${data.rates[r].formula}`} />
        ))}
        <Kpi label="Follow-ups cancelled" value={data.counts.followups_cancelled} sub="after reply / unsubscribe / bounce" />
        <Kpi label="Blocked sends" value={data.counts.blocked_sends} sub="suppression at queue or send time" />
      </div>
      <Card title="Segment & source analysis">
        <div className="tabs" role="tablist">
          {Object.keys(data.segments).map((d) => (
            <button key={d} role="tab" aria-selected={dim === d} onClick={() => setDim(d)}>
              By {d}
            </button>
          ))}
        </div>
        <SegmentTable rows={data.segments[dim] ?? []} />
      </Card>
      <Card
        title="Performance-to-improvement"
        actions={
          can("workflows.run") && (
            <ActionButton
              variant="primary"
              onClick={async () => {
                await startAndDrive(`/api/analytics/campaigns/${id}/improve`, {});
                await reload();
              }}
            >
              Generate ICP recommendations
            </ActionButton>
          )
        }
      >
        {!run ? (
          <Empty>Run the recommendation workflow once the campaign has outcomes.</Empty>
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            <p className="small">
              Run <StatusBadge status={run.status} /> {data.narrative} {data.narrative?.startsWith("[mock LLM]") && <MockTag label="mock LLM" />}
            </p>
            {data.recommendations.length === 0 ? (
              <Empty>No recommendation met the minimum-sample rules.</Empty>
            ) : (
              <ul className="small">
                {data.recommendations.map((r, i) => (
                  <li key={i}>
                    <Badge tone="accent">{humanize(r.kind)}</Badge> <strong>{r.summary}</strong> — {r.evidence}{" "}
                    {!r.applied_to_draft && <span className="muted">(review only)</span>}
                  </li>
                ))}
              </ul>
            )}
            {run.status === "waiting_approval" && (
              <Notice tone="warn">
                A new ICP version was proposed and waits for human review in <Link href="/approvals">Approvals</Link>. Nothing changes until then.
              </Notice>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

export default function AnalyticsPage() {
  const list = useApi<Campaign[]>("/api/campaigns");
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ?? list.data?.[0]?.id ?? null;
  return (
    <>
      <PageHeader
        title="Analytics"
        lead="Contact-level metrics with explicit numerators, denominators and formulas (tested in the backend). Zero denominators show n/a, not 0%."
        actions={
          list.data &&
          list.data.length > 1 && (
            <select value={selected ?? ""} onChange={(e) => setSel(e.target.value)} aria-label="Campaign">
              {list.data.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )
        }
      />
      {list.error ? (
        <ErrorBox error={list.error} />
      ) : !list.data ? (
        <Loading />
      ) : !selected ? (
        <Empty>
          No campaigns yet. Create one on <Link href="/campaigns">Campaigns</Link>.
        </Empty>
      ) : (
        <View key={selected} id={selected} />
      )}
    </>
  );
}
