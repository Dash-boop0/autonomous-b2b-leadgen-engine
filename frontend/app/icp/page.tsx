"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, startAndDrive } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { ICPVersion } from "@/lib/types";
import { ActionButton, Badge, Card, Disclosure, Empty, ErrorBox, Loading, MockTag, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { humanize, shortDate, validateWeights, weightShare } from "@/lib/format";

function Editor({ icp, onSaved }: { icp: ICPVersion; onSaved: () => void }) {
  const [weights, setWeights] = useState<Record<string, number>>(icp.weights);
  const [a, setA] = useState(icp.tier_thresholds.A);
  const [b, setB] = useState(icp.tier_thresholds.B);
  const [industries, setIndustries] = useState((icp.definition.industries ?? []).join(", "));
  const [summary, setSummary] = useState(icp.change_summary);
  useEffect(() => {
    setWeights(icp.weights);
    setA(icp.tier_thresholds.A);
    setB(icp.tier_thresholds.B);
    setIndustries((icp.definition.industries ?? []).join(", "));
    setSummary(icp.change_summary);
  }, [icp]);
  const err = validateWeights(weights);
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Criterion</th>
              <th className="num">Weight</th>
              <th className="num">Share</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(weights).map((k) => (
              <tr key={k}>
                <td>{humanize(k)}</td>
                <td className="num">
                  <input
                    type="number"
                    min={0}
                    step={1}
                    value={weights[k]}
                    aria-label={`Weight for ${k}`}
                    style={{ width: 80 }}
                    onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
                  />
                </td>
                <td className="num">{weightShare(weights, k)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row">
        <label className="field">
          Tier A ≥
          <input type="number" value={a} onChange={(e) => setA(Number(e.target.value))} style={{ width: 90 }} />
        </label>
        <label className="field">
          Tier B ≥
          <input type="number" value={b} onChange={(e) => setB(Number(e.target.value))} style={{ width: 90 }} />
        </label>
        <label className="field" style={{ flex: 1, minWidth: 220 }}>
          Target industries (comma-separated)
          <input value={industries} onChange={(e) => setIndustries(e.target.value)} />
        </label>
      </div>
      <label className="field">
        Change summary
        <input value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      {err && <Notice tone="bad">{err}</Notice>}
      <div className="row">
        <ActionButton
          disabled={!!err}
          onClick={async () => {
            await api.patch(`/api/icp/${icp.id}`, {
              weights,
              tier_thresholds: { A: a, B: b },
              industries: industries.split(",").map((s) => s.trim()).filter(Boolean),
              change_summary: summary,
            });
            onSaved();
          }}
        >
          Save draft
        </ActionButton>
        <ActionButton
          variant="primary"
          onClick={async () => {
            await api.post(`/api/icp/${icp.id}/submit`);
            onSaved();
          }}
        >
          Submit for approval
        </ActionButton>
      </div>
    </div>
  );
}

export default function ICPPage() {
  const { can } = useAuth();
  const list = useApi<ICPVersion[]>("/api/icp");
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ?? list.data?.find((i) => i.status === "approved")?.id ?? list.data?.[0]?.id ?? null;
  const detail = useApi<ICPVersion>(selected ? `/api/icp/${selected}` : null);
  const offers = useApi<{ id: string; name: string }[]>("/api/offers");

  if (list.loading && !list.data) return <Loading />;
  if (list.error) return <ErrorBox error={list.error} onRetry={list.reload} />;
  const reload = () => {
    void list.reload();
    void detail.reload();
  };
  const d = detail.data;
  const segments = (d?.research ?? []).filter((r) => r.kind === "segment");
  const personas = (d?.research ?? []).filter((r) => r.kind === "persona");
  const gaps = (d?.research ?? []).filter((r) => r.kind === "competitor_gap");
  return (
    <>
      <PageHeader
        title="ICP editor"
        lead="Versioned ideal-customer profiles and scoring models. Weighted criteria and hard disqualifiers are evaluated by deterministic code; edits create a new version that a reviewer must approve."
        actions={
          can("workflows.run") &&
          offers.data?.[0] && (
            <ActionButton
              onClick={async () => {
                await startAndDrive("/api/workflows/market_to_icp/runs", { input: { offer_id: offers.data![0].id } });
                reload();
              }}
            >
              Re-run market research
            </ActionButton>
          )
        }
      />
      <div className="split">
        <Card title="Versions">
          {(list.data ?? []).length === 0 ? (
            <Empty>No ICP versions yet.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Model version</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data!.map((i) => (
                    <tr key={i.id} className={`clickable${i.id === selected ? " is-selected" : ""}`} onClick={() => setSel(i.id)}>
                      <td>
                        <button className="linkbtn" onClick={() => setSel(i.id)}>
                          {i.name}
                        </button>
                      </td>
                      <td>
                        <StatusBadge status={i.status} />
                      </td>
                      <td className="mono small">{i.model_version}</td>
                      <td className="small">
                        {shortDate(i.created_at)}
                        <div className="muted">{i.created_by}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div className="stack">
          {detail.error && <ErrorBox error={detail.error} />}
          {!d ? (
            <Loading />
          ) : (
            <>
              <Card
                title={d.name}
                actions={
                  <>
                    <StatusBadge status={d.status} />
                    {can("icp.edit") && d.status !== "draft" && (
                      <ActionButton
                        size="sm"
                        onClick={async () => {
                          const f = await api.post<ICPVersion>(`/api/icp/${d.id}/fork`);
                          setSel(f.id);
                          reload();
                        }}
                      >
                        Edit as new draft
                      </ActionButton>
                    )}
                  </>
                }
              >
                {d.pending_approval_id && (
                  <Notice tone="warn">
                    Waiting for human approval. Go to <Link href="/approvals">Approvals</Link> as the reviewer.
                  </Notice>
                )}
                <p className="small">
                  {d.rationale} {d.rationale.startsWith("[mock LLM]") && <MockTag label="mock LLM" />}
                </p>
                {d.change_summary && <p className="small muted">Change: {d.change_summary}</p>}
                <div className="grid grid-2 small" style={{ marginTop: 8 }}>
                  <div>
                    <strong>Industries</strong>: {(d.definition.industries ?? []).join(", ")}
                    <div className="muted">adjacent: {(d.definition.adjacent_industries ?? []).join(", ")}</div>
                  </div>
                  <div>
                    <strong>Countries</strong>: {(d.definition.countries ?? []).join(", ")} · <strong>Size</strong>:{" "}
                    {(d.definition.employee_bands ?? []).join(", ")}
                  </div>
                  <div>
                    <strong>Buyer roles</strong>: {(d.definition.buyer_roles ?? []).map(humanize).join(", ")}
                  </div>
                  <div>
                    <strong>Signals</strong>: {(d.definition.signals ?? []).map(humanize).join(", ")}
                  </div>
                </div>
                <h3 style={{ marginTop: 12 }}>Hard disqualifiers (separate from weights)</h3>
                <ul className="small">
                  {d.hard_disqualifiers.map((h) => (
                    <li key={h.rule}>
                      <code>{h.rule}</code> {h.values ? `[${h.values.join(", ")}]` : ""} — {h.reason}
                    </li>
                  ))}
                </ul>
                <h3 style={{ marginTop: 12 }}>
                  Weights · tiers A ≥ {d.tier_thresholds.A}, B ≥ {d.tier_thresholds.B}
                </h3>
                {d.status === "draft" && can("icp.edit") ? (
                  <Editor icp={d} onSaved={reload} />
                ) : (
                  <div className="table-wrap">
                    <table>
                      <tbody>
                        {Object.entries(d.weights).map(([k, v]) => (
                          <tr key={k}>
                            <td>{humanize(k)}</td>
                            <td className="num">{v}</td>
                            <td style={{ width: "40%" }}>
                              <div className="bar" aria-label={`${weightShare(d.weights, k)}% of total weight`}>
                                <span style={{ width: `${weightShare(d.weights, k)}%` }} />
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
              <Card title="Research behind this ICP">
                {segments.length === 0 ? (
                  <Empty>No research artifacts.</Empty>
                ) : (
                  <div className="stack" style={{ gap: 10 }}>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Segment</th>
                            <th className="num">Fit</th>
                            <th className="num">Registry accounts</th>
                          </tr>
                        </thead>
                        <tbody>
                          {segments.map((s) => (
                            <tr key={s.id}>
                              <td>
                                {s.name}
                                <div className="small muted">{String(s.data.summary ?? "")}</div>
                              </td>
                              <td className="num">{Number(s.data.fit_score ?? 0).toFixed(2)}</td>
                              <td className="num">{String(s.data.account_count ?? "")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <Disclosure summary={`Buyer personas (${personas.length})`}>
                      <ul className="small">
                        {personas.map((p) => (
                          <li key={p.id}>
                            <strong>{p.name}</strong> <Badge>{humanize(String(p.data.buyer_role))}</Badge> — titles:{" "}
                            {(p.data.title_keywords as string[]).join(", ")}
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                    <Disclosure summary={`Competitor & market gaps (${gaps.length})`}>
                      <ul className="small">
                        {gaps.map((g) => (
                          <li key={g.id}>
                            <strong>{g.name}</strong>: {String(g.data.gap)}
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                    <Disclosure summary={`Evidence (${d.evidence?.length ?? 0})`}>
                      <ul className="small">
                        {(d.evidence ?? []).map((e) => (
                          <li key={e.id}>
                            {e.claim} {e.excerpt && <span className="muted">— “{e.excerpt}”</span>} <span className="muted">({e.confidence})</span>
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                  </div>
                )}
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  );
}
