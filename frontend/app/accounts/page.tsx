"use client";

import { useState } from "react";
import Link from "next/link";
import { useApi } from "@/lib/useApi";
import type { Account, AccountDetail } from "@/lib/types";
import { Badge, Card, Disclosure, Empty, ErrorBox, Loading, MockTag, PageHeader } from "@/components/ui";
import { VERIFICATION_LEVELS, formatValue, humanize, shortDate, tierTone } from "@/lib/format";

function Detail({ id }: { id: string }) {
  const { data, error, loading } = useApi<AccountDetail>(`/api/accounts/${id}`);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const { account: a, qualification: q } = data;
  const evById = new Map(data.evidence.map((e) => [e.id, e]));
  return (
    <div className="stack">
      <Card
        title={a.name}
        actions={q && <Badge tone={tierTone(q.tier)}>{q.tier === "disqualified" ? "Disqualified" : `Tier ${q.tier} · ${q.total_score_0_100}`}</Badge>}
      >
        <p className="small muted">
          {a.domain ?? "no domain"} · {a.industry ?? "industry unknown"} · {a.employee_band ?? "size unknown"} staff · {a.country ?? "?"} ·{" "}
          {a.business_model ?? "?"} · last verified {shortDate(a.last_verified_at)}
        </p>
        {!q ? (
          <Empty>Not scored yet.</Empty>
        ) : (
          <>
            {q.hard_disqualifiers.length > 0 && (
              <div className="notice notice-bad small">
                Hard disqualifiers (override the weighted score):{" "}
                {q.hard_disqualifiers.map((h) => (
                  <span key={h.rule}>
                    <code>{h.rule}</code> — {h.reason}{" "}
                  </span>
                ))}
              </div>
            )}
            <p className="small" style={{ marginTop: 8 }}>
              {q.explanation} {q.explanation.startsWith("[mock LLM]") && <MockTag label="mock LLM" />}
            </p>
            <p className="small muted">
              Model <code>{q.model_version}</code> · explained {shortDate(q.explained_at)} · score = Σ weight share × criterion score × 100
            </p>
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table>
                <thead>
                  <tr>
                    <th>Criterion</th>
                    <th>Value</th>
                    <th className="num">Weight</th>
                    <th className="num">Score</th>
                    <th className="num">Points</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {q.criteria.map((c) => (
                    <tr key={c.name}>
                      <td>
                        {humanize(c.name)}
                        {c.note && <div className="muted small">{c.note}</div>}
                      </td>
                      <td className="small wrap">{c.missing ? <Badge tone="warn">missing</Badge> : formatValue(c.raw_value)}</td>
                      <td className="num">{c.weight}</td>
                      <td className="num">{c.score_0_1.toFixed(2)}</td>
                      <td className="num">{c.contribution.toFixed(1)}</td>
                      <td className="small">
                        {c.evidence_ids.length === 0 ? (
                          <span className="muted">none</span>
                        ) : (
                          <Disclosure summary={`${c.evidence_ids.length} item(s)`}>
                            <ul>
                              {c.evidence_ids.slice(0, 4).map((eid) => (
                                <li key={eid}>{evById.get(eid)?.claim ?? eid}</li>
                              ))}
                            </ul>
                          </Disclosure>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      <Card title={`Contacts (${data.contacts.length})`}>
        {data.contacts.length === 0 ? (
          <Empty>
            No contacts yet. Run contact discovery from <Link href="/sources">Source adapters</Link>.
          </Empty>
        ) : (
          <ul className="steps small">
            {data.contacts.map((c) => (
              <li key={c.id}>
                <strong>{c.full_name}</strong> {c.title} · <Badge>{humanize(c.buyer_role)}</Badge>{" "}
                <Badge tone={VERIFICATION_LEVELS[c.email_status].tone}>{VERIFICATION_LEVELS[c.email_status].label}</Badge>
                {c.suppressed && <Badge tone="bad">suppressed</Badge>}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={`Evidence & provenance`}>
        <ul className="small">
          {data.evidence.map((e) => (
            <li key={e.id}>
              <Badge tone={e.kind === "signal" ? "accent" : "neutral"}>{e.kind}</Badge> {e.claim}{" "}
              {e.excerpt && <span className="muted wrap">— “{e.excerpt.slice(0, 160)}”</span>}{" "}
              <span className="muted">
                ({e.agent}, conf {e.confidence})
              </span>
            </li>
          ))}
        </ul>
        <h3 style={{ marginTop: 10 }}>Sources</h3>
        <ul className="small">
          {data.sources.map((s) => (
            <li key={s.id}>
              <code>{s.adapter}</code> · {s.source_type} · {s.url_or_ref} · conf {s.confidence} · {shortDate(s.retrieved_at)}
            </li>
          ))}
        </ul>
        {data.score_history.length > 1 && (
          <>
            <h3 style={{ marginTop: 10 }}>Score history</h3>
            <ul className="small">
              {data.score_history.map((h) => (
                <li key={h.id}>
                  {shortDate(h.at)} · {h.total} · tier {h.tier} · <code>{h.model_version}</code>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
    </div>
  );
}

export default function AccountsPage() {
  const [tier, setTier] = useState("");
  const [q, setQ] = useState("");
  const { data, error, loading, reload } = useApi<Account[]>(`/api/accounts${tier ? `?tier=${tier}` : ""}`);
  const [sel, setSel] = useState<string | null>(null);
  const rows = (data ?? []).filter((a) => !q || `${a.name} ${a.domain ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <PageHeader
        title="Accounts & scoring"
        lead="De-duplicated accounts from permitted sources. Every score shows its criteria contributions, missing data, evidence and model version; hard disqualifiers are listed separately."
      />
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field">
          Tier
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="">All</option>
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
            <option value="disqualified">Disqualified</option>
          </select>
        </label>
        <label className="field">
          Search
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="name or domain" />
        </label>
      </div>
      {loading && !data ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} onRetry={reload} />
      ) : rows.length === 0 ? (
        <Empty>
          No accounts yet. Approve the ICP, then run account discovery from <Link href="/sources">Source adapters</Link>.
        </Empty>
      ) : (
        <div className="split">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Industry</th>
                  <th>Size</th>
                  <th className="num">Score</th>
                  <th>Tier</th>
                  <th>Signals</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className={`clickable${sel === a.id ? " is-selected" : ""}`} onClick={() => setSel(a.id)}>
                    <td>
                      <button className="linkbtn" onClick={() => setSel(a.id)} aria-label={`Open ${a.name}`}>
                        {a.name}
                      </button>
                      <div className="muted small">
                        {a.domain ?? "no domain"} · {a.country ?? "?"} · {a.source_ids.length} source(s) · {a.contacts ?? 0} contact(s)
                      </div>
                    </td>
                    <td>{a.industry ?? "-"}</td>
                    <td>{a.employee_band ?? "-"}</td>
                    <td className="num">{a.score ?? "-"}</td>
                    <td>{a.tier ? <Badge tone={tierTone(a.tier)}>{a.tier === "disqualified" ? "DQ" : a.tier}</Badge> : "-"}</td>
                    <td className="small">
                      {a.signals === null ? <span className="muted">not researched</span> : a.signals.length ? a.signals.map(humanize).join(", ") : "none found"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>{sel ? <Detail id={sel} /> : <Empty>Select an account to see the score explanation and evidence.</Empty>}</div>
        </div>
      )}
    </>
  );
}
