"use client";

import { useApi } from "@/lib/useApi";
import type { AgentInfo } from "@/lib/types";
import { Card, Disclosure, ErrorBox, Json, Loading, PageHeader } from "@/components/ui";
import { groupBy, humanize } from "@/lib/format";

export default function AgentsPage() {
  const { data, error, loading, reload } = useApi<AgentInfo[]>("/api/agents");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <>
      <PageHeader
        title="Agents"
        lead="Twenty specialised agents. Each has typed input/output schemas, an explicit list of tools it may call (anything else is refused and audited), a confidence threshold that triggers review or abstention, retries and completion criteria. Agents can never approve, change permissions or bypass the approval engine."
      />
      <div className="stack">
        {groupBy(data ?? [], (a) => a.group).map(([group, agents]) => (
          <Card key={group} title={`${group} (${agents.length})`}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Tools (scoped)</th>
                    <th className="num">Threshold</th>
                    <th>Completion criteria</th>
                    <th>Used in</th>
                  </tr>
                </thead>
                <tbody>
                  {agents.map((a) => (
                    <tr key={a.name}>
                      <td>
                        <strong>{humanize(a.name)}</strong>
                        <div className="small muted wrap">{a.description}</div>
                        <Disclosure summary="I/O schemas">
                          <Json value={{ input: a.input_schema.properties, output: Object.keys(a.output_schema.properties as object) }} />
                        </Disclosure>
                      </td>
                      <td className="small">{a.tools.length ? a.tools.map((t) => <code key={t} style={{ display: "block" }}>{t}</code>) : <span className="muted">none (DB only)</span>}</td>
                      <td className="num">{a.confidence_threshold}</td>
                      <td className="small wrap">{a.completion_criteria}</td>
                      <td className="small">{a.workflows.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
