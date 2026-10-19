"use client";

import { useState } from "react";
import Link from "next/link";
import { API_URL, api, startAndDrive } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Adapter, CsvUpload, ICPVersion, Run, SourceRecord } from "@/lib/types";
import { ActionButton, Badge, Card, Empty, ErrorBox, Loading, MockTag, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { shortDate } from "@/lib/format";

function RunResult({ run }: { run: Run | null }) {
  if (!run) return null;
  return (
    <Notice tone={run.status === "completed" ? "good" : run.status === "failed" ? "bad" : "warn"}>
      {run.title}: <StatusBadge status={run.status} /> {run.error && <span className="wrap"> {run.error}</span>}{" "}
      <Link href="/workflows">See steps</Link>
    </Notice>
  );
}

function AdapterRow({ a, onSaved }: { a: Adapter; onSaved: () => void }) {
  const { can } = useAuth();
  const [allow, setAllow] = useState(a.allowlist.join(", "));
  const [block, setBlock] = useState(a.blocklist.join(", "));
  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  return (
    <tr>
      <td>
        <strong>{a.name}</strong> {a.mock && <MockTag />}
        <div className="muted small">
          <code>{a.id}</code> · {a.source_type} · {a.kind}
        </div>
        <div className="small wrap">{a.terms_note}</div>
      </td>
      <td>
        <Badge tone={a.enabled ? "good" : "bad"}>{a.enabled ? "enabled" : "disabled"}</Badge>
      </td>
      <td className="small">
        {a.rate_limit_per_minute}/min · cache {Math.round(a.cache_ttl_seconds / 3600)}h · retain {a.retention_days}d
        <div className="muted">auth: {a.auth}</div>
      </td>
      <td className="small">
        {can("sources.configure") ? (
          <div className="stack" style={{ gap: 4 }}>
            <input aria-label={`${a.id} allowlist`} value={allow} onChange={(e) => setAllow(e.target.value)} placeholder="allowlist" />
            <input aria-label={`${a.id} blocklist`} value={block} onChange={(e) => setBlock(e.target.value)} placeholder="blocklist" />
          </div>
        ) : (
          <>
            <div>allow: {a.allowlist.join(", ") || "any"}</div>
            <div>block: {a.blocklist.join(", ") || "none"}</div>
          </>
        )}
      </td>
      <td className="num">
        {a.records}
        <div className="muted small">{a.requests_total} req</div>
      </td>
      <td>
        {can("sources.configure") && (
          <div className="stack" style={{ gap: 4 }}>
            <ActionButton size="sm" onClick={async () => { await api.patch(`/api/sources/${a.id}`, { allowlist: split(allow), blocklist: split(block) }); onSaved(); }}>
              Save lists
            </ActionButton>
            <ActionButton size="sm" variant={a.enabled ? "danger" : "secondary"} onClick={async () => { await api.patch(`/api/sources/${a.id}`, { enabled: !a.enabled }); onSaved(); }}>
              {a.enabled ? "Disable" : "Enable"}
            </ActionButton>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function SourcesPage() {
  const { can } = useAuth();
  const adapters = useApi<Adapter[]>("/api/sources");
  const uploads = useApi<CsvUpload[]>("/api/sources/csv");
  const records = useApi<SourceRecord[]>("/api/sources/records?limit=50");
  const icps = useApi<ICPVersion[]>("/api/icp");
  const [kind, setKind] = useState<"accounts" | "contacts">("accounts");
  const [file, setFile] = useState<File | null>(null);
  const [accAdapters, setAccAdapters] = useState<string[]>(["csv_upload", "mock_registry"]);
  const [accUpload, setAccUpload] = useState("");
  const [conUpload, setConUpload] = useState("");
  const [lastRun, setLastRun] = useState<Run | null>(null);

  const approved = (icps.data ?? []).find((i) => i.status === "approved");
  const accUploads = (uploads.data ?? []).filter((u) => u.kind === "accounts");
  const conUploads = (uploads.data ?? []).filter((u) => u.kind === "contacts");
  const accSel = accUpload || accUploads[0]?.id || "";
  const conSel = conUpload || conUploads[0]?.id || "";
  const reloadAll = () => {
    void adapters.reload();
    void uploads.reload();
    void records.reload();
  };

  return (
    <>
      <PageHeader
        title="Source adapters"
        lead="Only permitted sources: CSV uploads, a mock registry API, a mock licensed contact dataset and local fixture pages standing in for permitted public pages. robots rules, allow/blocklists, rate limits and caching are enforced in code."
      />
      <div className="stack">
        <Card title="1 · Import & discover">
          {!approved ? (
            <Notice tone="warn">
              Discovery needs an approved ICP. Approve the research draft in <Link href="/approvals">Approvals</Link> first (reviewer role).
            </Notice>
          ) : (
            <div className="grid grid-2">
              <div className="stack" style={{ gap: 8 }}>
                <h3>Account discovery (ICP-to-account list)</h3>
                <p className="small muted">
                  Uses {approved.name}. Steps: discovery → normalisation → dedup → public-page evidence → buying signals → scoring.
                </p>
                <fieldset>
                  <legend>Adapters</legend>
                  {["csv_upload", "mock_registry"].map((id) => (
                    <label key={id} className="check" style={{ marginRight: 12 }}>
                      <input
                        type="checkbox"
                        checked={accAdapters.includes(id)}
                        onChange={(e) => setAccAdapters((s) => (e.target.checked ? [...s, id] : s.filter((x) => x !== id)))}
                      />
                      {id}
                    </label>
                  ))}
                </fieldset>
                <label className="field">
                  Accounts CSV
                  <select value={accSel} onChange={(e) => setAccUpload(e.target.value)} aria-label="Accounts CSV upload">
                    {accUploads.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.filename} ({u.row_count} rows)
                      </option>
                    ))}
                  </select>
                </label>
                <ActionButton
                  variant="primary"
                  disabled={!can("workflows.run") || accAdapters.length === 0}
                  title={can("workflows.run") ? undefined : "Operator role required"}
                  onClick={async () => {
                    setLastRun(
                      await startAndDrive("/api/workflows/icp_to_accounts/runs", {
                        input: { icp_version_id: approved.id, adapters: accAdapters, csv_upload_id: accAdapters.includes("csv_upload") ? accSel : null },
                      }),
                    );
                    reloadAll();
                  }}
                >
                  Run account discovery
                </ActionButton>
              </div>
              <div className="stack" style={{ gap: 8 }}>
                <h3>Contact discovery (Account-to-contact list)</h3>
                <p className="small muted">
                  For non-disqualified accounts: authorised discovery (CSV + mock licensed dataset) → role matching → enrichment → verification →
                  qualification → suppression checks.
                </p>
                <label className="field">
                  Contacts CSV
                  <select value={conSel} onChange={(e) => setConUpload(e.target.value)} aria-label="Contacts CSV upload">
                    {conUploads.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.filename} ({u.row_count} rows)
                      </option>
                    ))}
                  </select>
                </label>
                <ActionButton
                  variant="primary"
                  disabled={!can("workflows.run")}
                  onClick={async () => {
                    setLastRun(
                      await startAndDrive("/api/workflows/account_to_contacts/runs", {
                        input: { icp_version_id: approved.id, adapters: ["csv_upload", "licensed_contacts"], csv_upload_id: conSel || null },
                      }),
                    );
                    reloadAll();
                  }}
                >
                  Run contact discovery
                </ActionButton>
              </div>
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            <RunResult run={lastRun} />
          </div>
        </Card>

        <Card title="2 · CSV uploads" actions={<span className="small muted">Stored in the database (no filesystem writes). Max 2 MB.</span>}>
          {can("sources.import") && (
            <form
              className="row"
              style={{ marginBottom: 12 }}
              onSubmit={async (e) => {
                e.preventDefault();
                if (!file) return;
                const fd = new FormData();
                fd.append("kind", kind);
                fd.append("file", file);
                try {
                  await api.upload("/api/sources/csv", fd);
                  setFile(null);
                  (e.target as HTMLFormElement).reset();
                  void uploads.reload();
                } catch (err) {
                  window.alert(err instanceof Error ? err.message : String(err));
                }
              }}
            >
              <select value={kind} onChange={(e) => setKind(e.target.value as "accounts" | "contacts")} aria-label="CSV kind">
                <option value="accounts">accounts</option>
                <option value="contacts">contacts</option>
              </select>
              <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <button className="btn" type="submit" disabled={!file}>
                Upload
              </button>
              <span className="small muted">
                Templates: <a href={`${API_URL}/api/sources/samples/accounts.csv`}>accounts.csv</a> ·{" "}
                <a href={`${API_URL}/api/sources/samples/contacts.csv`}>contacts.csv</a> (synthetic)
              </span>
            </form>
          )}
          {uploads.error ? (
            <ErrorBox error={uploads.error} />
          ) : !uploads.data ? (
            <Loading />
          ) : uploads.data.length === 0 ? (
            <Empty>No uploads.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Kind</th>
                    <th className="num">Rows</th>
                    <th>Uploaded</th>
                    <th>Used</th>
                    <th>SHA-256</th>
                  </tr>
                </thead>
                <tbody>
                  {uploads.data.map((u) => (
                    <tr key={u.id}>
                      <td>{u.filename}</td>
                      <td>{u.kind}</td>
                      <td className="num">{u.row_count}</td>
                      <td>
                        {shortDate(u.uploaded_at)} <span className="muted small">by {u.uploaded_by}</span>
                      </td>
                      <td>{u.consumed_at ? shortDate(u.consumed_at) : "-"}</td>
                      <td className="mono small">{u.sha256.slice(0, 12)}…</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="3 · Adapters">
          {adapters.error ? (
            <ErrorBox error={adapters.error} onRetry={adapters.reload} />
          ) : !adapters.data ? (
            <Loading />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Adapter</th>
                    <th>State</th>
                    <th>Limits</th>
                    <th>Allow / block</th>
                    <th className="num">Records</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {adapters.data.map((a) => (
                    <AdapterRow key={a.id} a={a} onSaved={adapters.reload} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="small muted" style={{ marginTop: 8 }}>
            Field lists, restrictions and failure handling per adapter are documented in docs/DATA-SOURCES.md.
          </p>
        </Card>

        <Card title="4 · Provenance (latest source records)">
          {records.error ? (
            <ErrorBox error={records.error} />
          ) : !records.data ? (
            <Loading />
          ) : records.data.length === 0 ? (
            <Empty>No source records yet. Run a discovery workflow.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Adapter</th>
                    <th>Type</th>
                    <th>Reference</th>
                    <th className="num">Confidence</th>
                    <th>Retrieved</th>
                  </tr>
                </thead>
                <tbody>
                  {records.data.map((r) => (
                    <tr key={r.id}>
                      <td>{r.adapter}</td>
                      <td>{r.source_type}</td>
                      <td className="wrap small">{r.url_or_ref}</td>
                      <td className="num">{r.confidence.toFixed(2)}</td>
                      <td>{shortDate(r.retrieved_at)}</td>
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
