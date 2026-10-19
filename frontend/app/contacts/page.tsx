"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Contact, EmailStatus } from "@/lib/types";
import { ActionButton, Badge, Card, Disclosure, Empty, ErrorBox, Loading, MockTag, PageHeader, StatusBadge } from "@/components/ui";
import { VERIFICATION_LEVELS, humanize } from "@/lib/format";

export default function ContactsPage() {
  const { can } = useAuth();
  const [status, setStatus] = useState("");
  const [role, setRole] = useState("");
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (role) params.set("role", role);
  const { data, error, loading, reload } = useApi<Contact[]>(`/api/contacts${params.size ? `?${params}` : ""}`);
  return (
    <>
      <PageHeader
        title="Contacts"
        lead="Contacts come only from authorised sources (user CSV, mock licensed dataset). Each verification level is a different claim and is labelled honestly."
      />
      <Card title="What each verification level means">
        <div className="grid grid-3 small">
          {(Object.keys(VERIFICATION_LEVELS) as EmailStatus[]).map((k) => (
            <div key={k}>
              <Badge tone={VERIFICATION_LEVELS[k].tone}>{VERIFICATION_LEVELS[k].label}</Badge> <span className="muted">{VERIFICATION_LEVELS[k].meaning}</span>
            </div>
          ))}
        </div>
      </Card>
      <div className="row" style={{ margin: "12px 0" }}>
        <label className="field">
          Verification
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {Object.keys(VERIFICATION_LEVELS).map((k) => (
              <option key={k} value={k}>
                {VERIFICATION_LEVELS[k as EmailStatus].label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Buyer role
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">All</option>
            {["economic_buyer", "champion", "technical", "user", "unknown"].map((r) => (
              <option key={r} value={r}>
                {humanize(r)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {loading && !data ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} onRetry={reload} />
      ) : !data || data.length === 0 ? (
        <Empty>
          No contacts. Run contact discovery from <Link href="/sources">Source adapters</Link>.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Contact</th>
                <th>Account</th>
                <th>Buyer role</th>
                <th>Email & verification</th>
                <th>Lead</th>
                {can("contacts.erase") && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((c) => {
                const lvl = VERIFICATION_LEVELS[c.email_status];
                return (
                  <tr key={c.id}>
                    <td>
                      <strong>{c.full_name}</strong>
                      <div className="muted small">
                        {c.title ?? "no title"} · {c.seniority ?? "?"} · {c.department ?? "?"}
                      </div>
                    </td>
                    <td className="small">{c.account_name}</td>
                    <td>
                      <Badge tone={c.buyer_role === "unknown" ? "warn" : "accent"}>{humanize(c.buyer_role)}</Badge>
                      <div className="muted small wrap">{c.role_match_reason}</div>
                    </td>
                    <td className="small">
                      <div className="mono wrap">{c.email ?? "-"}</div>
                      <Badge tone={lvl.tone} title={lvl.meaning}>
                        {lvl.label}
                      </Badge>{" "}
                      {c.suppressed && <Badge tone="bad">suppressed: {c.suppression_reason}</Badge>}
                      {(c.verification_checks ?? []).length > 0 && (
                        <Disclosure summary="checks">
                          <ul>
                            {c.verification_checks!.map((v, i) => (
                              <li key={i}>
                                {v.check}: <strong>{v.result}</strong> via {v.provider} {v.mock && <MockTag />} {v.simulated && <MockTag label="simulated" />}
                              </li>
                            ))}
                          </ul>
                        </Disclosure>
                      )}
                    </td>
                    <td>{c.lead_stage ? <StatusBadge status={c.lead_stage} /> : <span className="muted">-</span>}</td>
                    {can("contacts.erase") && (
                      <td>
                        <ActionButton
                          size="sm"
                          variant="danger"
                          confirm={`Erase ${c.full_name}? The address is added to the suppression list and the record is deleted.`}
                          onClick={async () => {
                            await api.post(`/api/contacts/${c.id}/erase`);
                            await reload();
                          }}
                        >
                          Erase
                        </ActionButton>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
