"use client";

import { useState } from "react";
import Link from "next/link";
import { api, startAndDrive } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Campaign, CampaignDetail, Draft, ICPVersion, Run } from "@/lib/types";
import { ActionButton, Badge, Card, Disclosure, Empty, ErrorBox, Loading, MockTag, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { groupBy, humanize, shortDate } from "@/lib/format";

function CreateForm({ onCreated }: { onCreated: (id: string, run: Run) => void }) {
  const icps = useApi<ICPVersion[]>("/api/icp");
  const approved = (icps.data ?? []).filter((i) => i.status === "approved");
  const [name, setName] = useState("Q4 demand-planning intro (synthetic)");
  const [tiers, setTiers] = useState<string[]>(["A", "B"]);
  const [delay, setDelay] = useState(3);
  const [cap, setCap] = useState(2);
  if (icps.loading && !icps.data) return <Loading />;
  if (approved.length === 0) return <Notice tone="warn">Approve an ICP first.</Notice>;
  return (
    <form
      className="stack"
      style={{ gap: 10 }}
      onSubmit={(e) => e.preventDefault()}
      aria-label="Create campaign"
    >
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="row">
        <fieldset>
          <legend>Tiers</legend>
          {["A", "B", "C"].map((t) => (
            <label key={t} className="check" style={{ marginRight: 10 }}>
              <input type="checkbox" checked={tiers.includes(t)} onChange={(e) => setTiers((s) => (e.target.checked ? [...s, t] : s.filter((x) => x !== t)))} />
              {t}
            </label>
          ))}
        </fieldset>
        <label className="field">
          Follow-up after (days)
          <input type="number" min={1} max={30} value={delay} onChange={(e) => setDelay(Number(e.target.value))} style={{ width: 90 }} />
        </label>
        <label className="field">
          Max per contact / 7 days
          <input type="number" min={1} max={5} value={cap} onChange={(e) => setCap(Number(e.target.value))} style={{ width: 90 }} />
        </label>
      </div>
      <ActionButton
        variant="primary"
        disabled={tiers.length === 0 || name.length < 3}
        onClick={async () => {
          const res = await api.post<{ campaign: Campaign; run: Run }>("/api/campaigns", {
            name,
            icp_version_id: approved[0].id,
            tiers,
            follow_up_delay_days: delay,
            max_per_contact_per_7d: cap,
          });
          onCreated(res.campaign.id, res.run);
        }}
      >
        Create & draft (Lead-to-campaign)
      </ActionButton>
      <p className="small muted">
        Uses {approved[0].name}. Agents segment qualified leads, draft evidence-cited emails and run policy checks; the workflow then stops for a
        reviewer.
      </p>
    </form>
  );
}

function DraftCard({ d, canReview, onChange }: { d: Draft; canReview: boolean; onChange: () => void }) {
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="row">
        <strong>Step {d.step_no}</strong>
        <Badge tone={d.personalisation_level === "evidence_grounded" ? "good" : "neutral"}>{humanize(d.personalisation_level)}</Badge>
        <Badge tone={d.policy_passed ? "good" : "bad"}>{d.policy_passed ? "policy passed" : "policy failed"}</Badge>
        <StatusBadge status={d.status} />
        {d.llm_provider === "mock-llm" && <MockTag label="mock LLM" />}
      </div>
      <p className="small" style={{ marginTop: 6 }}>
        <strong>Subject:</strong> {d.subject}
      </p>
      <div className="email">{d.body}</div>
      {d.evidence.length > 0 && (
        <div className="small" style={{ marginTop: 6 }}>
          <strong>Cited evidence:</strong>
          <ul>
            {d.evidence.map((e) => (
              <li key={e.id}>
                {e.claim} <span className="muted">— “{e.excerpt}” (conf {e.confidence})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Disclosure summary="Policy checks">
        <ul className="small">
          {d.policy_checks.map((p) => (
            <li key={p.rule}>
              <Badge tone={p.passed ? "good" : "bad"}>{p.passed ? "pass" : "fail"}</Badge> {humanize(p.rule)} {p.detail && <span className="muted">— {p.detail}</span>}
            </li>
          ))}
        </ul>
      </Disclosure>
      {canReview && (d.status === "draft" || d.status === "rejected") && (
        <div style={{ marginTop: 6 }}>
          <ActionButton
            size="sm"
            variant={d.status === "rejected" ? "secondary" : "danger"}
            onClick={async () => {
              await api.post(`/api/drafts/${d.id}/review`, { status: d.status === "rejected" ? "draft" : "rejected", reason: "reviewer decision" });
              onChange();
            }}
          >
            {d.status === "rejected" ? "Restore draft" : "Exclude draft"}
          </ActionButton>
        </div>
      )}
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const { can, refreshSystem, system } = useAuth();
  const { data, error, loading, reload } = useApi<CampaignDetail>(`/api/campaigns/${id}`);
  const [last, setLast] = useState<Run | null>(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;
  const c = data.campaign;
  const grounded = (ds: Draft[]) => (ds.some((d) => d.personalisation_level === "evidence_grounded") ? 0 : 1);
  const grouped = groupBy(data.drafts, (d) => `${d.contact_name} · ${d.account_name}`).sort((x, y) => grounded(x[1]) - grounded(y[1]));
  const counts = data.queue.reduce<Record<string, number>>((m, q) => ((m[q.status] = (m[q.status] ?? 0) + 1), m), {});
  const live = c.status === "active" || c.status === "paused";
  return (
    <div className="stack">
      <Card
        title={c.name}
        actions={
          <>
            <StatusBadge status={c.status} />
            {c.status === "pending_approval" && (
              <Link className="btn btn-sm btn-primary" href="/approvals">
                Go to approval
              </Link>
            )}
          </>
        }
      >
        <div className="grid grid-2 small">
          <div>
            <strong>Sequence</strong>:{" "}
            {c.sequence.map((s) => `step ${s.step} (+${s.delay_days}d, ${s.template})`).join(" → ")}
            <div>
              <strong>Caps</strong>: {c.frequency_cap.max_per_contact_per_7d}/contact/7d · {c.frequency_cap.max_sends_per_day}/day
            </div>
            <div>
              <strong>Schedule</strong>: {Object.values(c.schedule).join(" · ")}
            </div>
          </div>
          <div>
            <strong>Segments</strong>:{" "}
            {(c.strategy.segments ?? []).map((s) => `${s.name} (${s.lead_ids.length}) — ${s.angle}`).join("; ") || "-"}
            <div className="muted">
              Excluded:{" "}
              {Object.entries(c.strategy.excluded ?? {})
                .map(([k, v]) => `${humanize(k)} ${v}`)
                .join(", ") || "none"}
            </div>
          </div>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {Object.entries(counts).map(([k, v]) => (
            <Badge key={k} tone={k === "sent_simulated" ? "good" : k === "queued" ? "accent" : "bad"}>
              {humanize(k)}: {v}
            </Badge>
          ))}
        </div>
        {can("events.simulate") && (
          <div className="row" style={{ marginTop: 10 }}>
            <ActionButton
              variant="primary"
              disabled={!live || !!system?.emergency_stop}
              title={!live ? "Approve the campaign first" : system?.emergency_stop ? "Emergency stop is on" : undefined}
              onClick={async () => {
                setLast(await startAndDrive(`/api/campaigns/${c.id}/conversation-cycle`, {}));
                await reload();
              }}
            >
              Run send + inbox cycle (dry-run)
            </ActionButton>
            <ActionButton
              disabled={!live}
              onClick={async () => {
                await api.post("/api/system/sim-clock", { days: 3 });
                await refreshSystem();
              }}
            >
              Advance simulated clock +3 days
            </ActionButton>
            {can("campaigns.manage") && live && (
              <ActionButton
                onClick={async () => {
                  await api.post(`/api/campaigns/${c.id}/pause`, { paused: c.status === "active" });
                  await reload();
                }}
              >
                {c.status === "active" ? "Pause campaign" : "Resume campaign"}
              </ActionButton>
            )}
          </div>
        )}
        {last && (
          <div style={{ marginTop: 8 }}>
            <Notice tone={last.status === "completed" ? "good" : "warn"}>
              Campaign-to-conversation: <StatusBadge status={last.status} />{" "}
              {last.steps.map((s) => `${s.name}:${s.status}`).join(" · ")} — see <Link href="/replies">Reply triage</Link>.
            </Notice>
          </div>
        )}
      </Card>

      <Card title={`Send queue / outbox (${data.queue.length})`} actions={<MockTag label="dry-run: nothing leaves this system" />}>
        {data.queue.length === 0 ? (
          <Empty>Nothing queued. Messages are queued only after a reviewer approves the campaign.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Step</th>
                  <th>To</th>
                  <th>Scheduled</th>
                  <th>Status</th>
                  <th>Reason / message id</th>
                </tr>
              </thead>
              <tbody>
                {data.queue.map((q) => (
                  <tr key={q.id}>
                    <td>{q.step_no}</td>
                    <td className="mono small">{q.to_email}</td>
                    <td className="small">{shortDate(q.scheduled_for)}</td>
                    <td>
                      <StatusBadge status={q.status} />
                    </td>
                    <td className="small wrap">{q.status_reason ?? q.provider_message_id ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={`Drafts (${data.drafts.length})`}>
        {grouped.length === 0 ? (
          <Empty>No drafts yet.</Empty>
        ) : (
          <div className="stack">
            {grouped.map(([who, drafts]) => (
              <Disclosure key={who} summary={`${who} — ${drafts.length} step(s)${grounded(drafts) === 0 ? " · evidence-grounded" : " · generic"}${drafts.some((d) => d.status === "rejected") ? " (excluded)" : ""}`}>
                <div className="stack" style={{ gap: 8 }}>
                  {drafts.map((d) => (
                    <DraftCard key={d.id} d={d} canReview={can("approvals.decide") && c.status === "pending_approval"} onChange={reload} />
                  ))}
                </div>
              </Disclosure>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

export default function CampaignsPage() {
  const { can } = useAuth();
  const list = useApi<Campaign[]>("/api/campaigns");
  const [sel, setSel] = useState<string | null>(null);
  const [created, setCreated] = useState<Run | null>(null);
  const selected = sel ?? list.data?.[0]?.id ?? null;
  return (
    <>
      <PageHeader
        title="Campaigns"
        lead="Sequences, templates, schedules and frequency caps. Default is dry-run: approved messages go to an outbox table, delivery and replies are simulated, and follow-ups are cancelled after replies, unsubscribes, bounces or disqualification."
      />
      <div className="stack">
        <div className="grid grid-2">
          {can("campaigns.manage") && (
            <Card title="New campaign">
              <CreateForm
                onCreated={(id, run) => {
                  setSel(id);
                  setCreated(run);
                  void list.reload();
                }}
              />
              {created && (
                <div style={{ marginTop: 8 }}>
                  <Notice tone={created.status === "waiting_approval" ? "warn" : created.status === "failed" ? "bad" : "good"}>
                    Lead-to-campaign: <StatusBadge status={created.status} /> {created.error}
                    {created.status === "waiting_approval" && " — switch to the reviewer and approve it."}
                  </Notice>
                </div>
              )}
            </Card>
          )}
          <Card title="All campaigns">
            {list.error ? (
              <ErrorBox error={list.error} onRetry={list.reload} />
            ) : !list.data ? (
              <Loading />
            ) : list.data.length === 0 ? (
              <Empty>No campaigns yet.</Empty>
            ) : (
              <ul className="steps">
                {list.data.map((c) => (
                  <li key={c.id}>
                    <button className={`btn btn-sm ${c.id === selected ? "btn-primary" : "btn-ghost"}`} onClick={() => setSel(c.id)}>
                      {c.name}
                    </button>
                    <StatusBadge status={c.status} />
                    <span className="muted small">{c.drafts} drafts</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        {selected ? <Detail key={selected} id={selected} /> : <Empty>Create or select a campaign.</Empty>}
      </div>
    </>
  );
}
