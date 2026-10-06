/**
 * Mission overview: what the daemon knows right now.
 *
 * This is the landing surface, and it is deliberately a *status* page rather than a
 * dashboard of invented metrics: store state, what boot reconciliation found, and the most
 * recent events. Cost, tokens and heartbeats are absent because nothing measures them yet.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "../api";
import { EventTable } from "../components/EventTable";
import { EmptyState, Failure, KeyValues, Loading, Panel } from "../components/Panel";
import { useStream } from "../stream";

export function MissionOverviewPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 5000 });
  const stream = useStream();

  if (health.isLoading) return <Loading label="reading daemon state" />;
  if (health.isError) return <Failure label="daemon" error={health.error} />;
  const data = health.data;
  if (!data) return <Failure label="daemon" error="no health payload" />;

  const recent = [...stream.events].slice(-15).reverse();

  return (
    <>
      <div className="grid">
        <Panel title="daemon" hint={`${data.service} v${data.version}`}>
          <KeyValues
            rows={[
              ["status", <span className="ok">{data.status}</span>],
              ["git sha", <span className="mono">{data.git_sha || "—"}</span>],
              ["uptime", `${Math.round(data.uptime_s)}s`],
              ["data root", <span className="mono">{data.data_root}</span>],
              ["web bundle", data.web_bundle ? <span className="mono">{data.web_bundle}</span> : <span className="faint">API-only (no bundle built)</span>],
            ]}
          />
        </Panel>

        <Panel title="canonical store" hint="ADR-0003">
          <KeyValues
            rows={[
              ["schema", `v${data.store.schema_version ?? "?"}`],
              ["events", String(data.store.events ?? 0)],
              ["journal", <span className="mono">{data.store.journal_mode ?? "—"}</span>],
              ["path", <span className="mono">{data.store.path ?? "—"}</span>],
            ]}
          />
          <p className="tight faint">
            Every event on this page was committed to SQLite before it was delivered. Closing and
            reopening the daemon does not lose the history.
          </p>
        </Panel>

        <Panel title="boot reconciliation" hint="BOOK §83">
          {data.reconcile ? (
            <>
              <KeyValues
                rows={[
                  ["runs examined", String(data.reconcile.runs_examined)],
                  ["orphans marked", String(data.reconcile.orphans_marked)],
                  ["leases released", String(data.reconcile.leases_released)],
                  ["artifacts intact", String(data.reconcile.artifacts_preserved)],
                ]}
              />
              {data.reconcile.artifacts_missing.length > 0 ? (
                <p className="tight error">
                  missing artifacts: {data.reconcile.artifacts_missing.join(", ")}
                </p>
              ) : null}
              <p className="tight faint">
                Runs left in <code>running</code> by a dead process become <code>orphaned</code> through a
                new event. Reconciliation never resumes work by itself.
              </p>
            </>
          ) : (
            <EmptyState title="no report">
              Reconciliation runs at startup and records its report as an event.
            </EmptyState>
          )}
        </Panel>
      </div>

      <Panel
        title="recent activity"
        hint={
          stream.state === "live"
            ? "live socket"
            : stream.state === "connecting"
              ? "connecting"
              : "socket down"
        }
      >
        <EventTable events={recent} />
      </Panel>
    </>
  );
}
