/**
 * Execution boundary: what is actually enforced for this session, and what is not (M3).
 *
 * The rule this panel follows: a badge is a claim, so it is rendered from the daemon's evidence and
 * never from a default. `WEAK` and `NOT ENFORCED` are first-class outcomes — a session with no
 * sandbox shows them, and the reason next to them, instead of a reassuring green.
 */

import { useQuery } from "@tanstack/react-query";
import { BudgetRecordRow, SessionPolicy, fetchSandboxProviders, fetchSessionPolicy } from "../api";
import { EmptyState, KeyValues, Loading, Panel } from "./Panel";

function levelClass(level: string | undefined): string {
  switch (level) {
    case "strong":
      return "badge badge-strong";
    case "moderate":
      return "badge badge-moderate";
    case "weak":
      return "badge badge-weak";
    default:
      return "badge badge-unknown";
  }
}

function Level({ level }: { level: string | undefined }) {
  const label = (level ?? "unknown").toUpperCase();
  return <span className={levelClass(level)}>{label}</span>;
}

function budgetLabel(record: BudgetRecordRow): string {
  const observed = record.observed === null || record.observed === undefined ? "?" : Math.round(record.observed * 100) / 100;
  const requested = record.requested === null || record.requested === undefined ? "no limit" : record.requested;
  const unit: Record<string, string> = {
    wall_time: "s",
    tool_calls: "",
    tokens: "",
    cost: "",
    child_processes: "",
  };
  const suffix = unit[record.kind] ?? "";
  return `${observed}${suffix} / ${requested}${suffix}`;
}

function BudgetTable({ records, exceeded }: { records: BudgetRecordRow[]; exceeded: Record<string, unknown> }) {
  if (records.length === 0) {
    return (
      <EmptyState title="no budgets on this session">
        No limit was requested, so nothing is being enforced. That is recorded as it is — an
        unrequested budget is not the same as an unlimited one that is under control.
      </EmptyState>
    );
  }
  return (
    <table>
      <thead>
        <tr>
          <th>budget</th>
          <th>observed / requested</th>
          <th>enforcement</th>
          <th>what happens at the limit</th>
        </tr>
      </thead>
      <tbody>
        {records.map((record) => {
          const fired = Boolean(exceeded[record.kind]) || record.limit_reached;
          return (
            <tr key={record.kind}>
              <td className="mono">{record.kind}</td>
              <td className={fired ? "error" : ""}>{budgetLabel(record)}</td>
              <td>
                <Level level={record.enforcement} />
                <div className="faint">{record.enforcement_mode}</div>
              </td>
              <td className="faint">
                {fired ? (
                  <>
                    <span className="error">LIMIT REACHED</span> — {record.action ?? "no action recorded"}
                  </>
                ) : record.enforcement === "weak" ? (
                  "SOFT LIMIT: recorded after the fact, not interruptible"
                ) : (
                  "enforced while the session runs"
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ExecutionBoundaryPanel({ sessionId }: { sessionId: string | null }) {
  const policy = useQuery({
    queryKey: ["policy", sessionId],
    queryFn: () => fetchSessionPolicy(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: 4000,
  });
  const sandbox = useQuery({ queryKey: ["sandbox"], queryFn: fetchSandboxProviders, refetchInterval: 30000 });

  if (!sessionId) {
    return (
      <Panel title="execution boundary" hint="no session">
        <EmptyState title="nothing is running">
          Start a session to see its isolation and its budgets. Without a session there is nothing
          to enforce, and this panel will not invent a number for it.
        </EmptyState>
      </Panel>
    );
  }
  if (policy.isLoading) return <Loading label="reading the policy" />;

  const data: SessionPolicy | undefined = policy.data;
  const evidence = data?.evidence ?? undefined;
  const provider = evidence?.provider ?? "none";
  const failed = (evidence?.checks ?? []).filter((check) => !check.ok);
  const notEnforced = Object.entries(evidence?.as_badges ?? {}).filter(([, level]) => level === "weak");

  return (
    <>
      <Panel title="execution boundary" hint={provider === "none" ? "no sandbox" : `provider: ${provider}`}>
        {data ? (
          <>
            <KeyValues
              rows={[
                ["isolation", <Level level={data.isolation ?? undefined} />],
                ["filesystem", <Level level={evidence?.filesystem} />],
                ["network", <Level level={evidence?.network} />],
                ["workspace", <span className="mono">{data.effective?.workspace ?? "—"}</span>],
                ["mode", <span className="mono">{data.effective?.filesystem_mode ?? "—"}</span>],
                [
                  "checked",
                  <span className="faint">
                    {(evidence?.checks ?? []).filter((check) => check.ok).length} passed ·{" "}
                    <span className={failed.length ? "error" : ""}>{failed.length} failed</span>
                  </span>,
                ],
              ]}
            />
            {failed.length > 0 ? (
              <ul className="facts">
                {failed.map((check) => (
                  <li key={check.name} className="error">
                    {check.name}: {check.detail}
                  </li>
                ))}
              </ul>
            ) : null}
            {evidence?.note ? <p className="tight faint">{evidence.note}</p> : null}
          </>
        ) : (
          <EmptyState title="no policy recorded">
            This session was created without a policy, so nothing was enforced. The daemon records
            that as `weak` rather than assuming protection.
          </EmptyState>
        )}
      </Panel>

      <Panel title="budgets" hint={data?.live ? "live" : "recorded"}>
        {data ? <BudgetTable records={data.budgets ?? []} exceeded={data.exceeded ?? {}} /> : null}
      </Panel>

      <Panel title="sandbox providers" hint="why this session is as strong as it is">
        {sandbox.data ? (
          <ul className="facts">
            {sandbox.data.providers.map((row) => (
              <li key={row.provider}>
                <span className={row.available ? "ok" : "faint"}>
                  {row.available ? "available" : "unavailable"}
                </span>{" "}
                <span className="mono">{row.provider}</span>{" "}
                <span className="faint">{row.detail}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Loading />
        )}
        {notEnforced.length > 0 ? (
          <p className="tight faint">
            not enforced on this session:{" "}
            {notEnforced.map(([dimension]) => dimension).join(", ")}
          </p>
        ) : null}
      </Panel>
    </>
  );
}
