/**
 * Runtime: everything the daemon will tell you about itself.
 *
 * This page is where "is it healthy?" gets an answer with paths, versions and journal modes
 * rather than a green dot. It reads `/health` and `/version` and shows both verbatim.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchHealth, fetchVersion, namespaceOf } from "../api";
import { EmptyState, Failure, KeyValues, Loading, Panel } from "../components/Panel";
import { useStream } from "../stream";

export function SystemPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 5000 });
  const version = useQuery({ queryKey: ["version"], queryFn: fetchVersion });
  const stream = useStream();

  if (health.isLoading) return <Loading label="reading daemon state" />;
  if (health.isError) return <Failure label="daemon" error={health.error} />;
  const data = health.data;
  if (!data) return <Failure label="daemon" error="no health payload" />;

  const namespaces = [...new Set(stream.events.map((event) => namespaceOf(event.kind)))].sort();

  return (
    <>
      <div className="grid">
        <Panel title="runtime" hint={version.data ? `${version.data.name} ${version.data.version}` : "…"}>
          {version.isError ? (
            <p className="error">{version.error instanceof Error ? version.error.message : "unavailable"}</p>
          ) : version.data ? (
            <KeyValues
              rows={[
                ["python", <span className="mono">{version.data.python}</span>],
                ["platform", <span className="mono">{version.data.platform}</span>],
                ["git branch", <span className="mono">{version.data.git_branch || "—"}</span>],
                ["git sha", <span className="mono">{version.data.git_sha || "—"}</span>],
                ["api", <span className="mono">{version.data.api_version}</span>],
              ]}
            />
          ) : (
            <Loading />
          )}
        </Panel>

        <Panel title="storage" hint="SQLite WAL">
          <KeyValues
            rows={[
              ["schema version", `v${data.store.schema_version ?? "?"}`],
              ["events", String(data.store.events ?? 0)],
              ["journal mode", <span className="mono">{data.store.journal_mode ?? "—"}</span>],
              ["database", <span className="mono">{data.store.path ?? "—"}</span>],
              ["data root", <span className="mono">{data.data_root}</span>],
            ]}
          />
        </Panel>

        <Panel title="stream" hint={`socket ${stream.state}`}>
          <KeyValues
            rows={[
              ["frames buffered", String(stream.events.length)],
              ["last seq", String(stream.lastSeq)],
              ["subscribers", String(data.events.subscribers)],
              ["reconnects", String(stream.reconnectAttempts)],
              ["error", stream.error ? <span className="error">{stream.error}</span> : <span className="faint">none</span>],
            ]}
          />
        </Panel>
      </div>

      <Panel title="namespaces seen" hint="BOOK §13">
        {namespaces.length === 0 ? (
          <EmptyState title="nothing observed yet">
            Namespaces appear here as events arrive over the socket.
          </EmptyState>
        ) : (
          <p className="tight mono">{namespaces.join("  ·  ")}</p>
        )}
      </Panel>
    </>
  );
}
