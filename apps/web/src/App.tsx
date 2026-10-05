import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  EventEnvelope,
  fetchHealth,
  fetchRecentEvents,
  fetchVersion,
  openEventStream,
} from "./api";

/**
 * WP-002 placeholder shell: it proves the wiring (HTTP + WebSocket + explicit
 * failure states) and nothing more. The real information architecture — roster,
 * mission surfaces, inspector — is WP-006 (`docs/work-packages/WP-006-*.md`).
 */
export function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 5000 });
  const version = useQuery({ queryKey: ["version"], queryFn: fetchVersion });
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [streamState, setStreamState] = useState<"connecting" | "live" | "polling">("connecting");

  useEffect(() => {
    const dispose = openEventStream((event) => {
      setStreamState("live");
      setEvents((current) => [event, ...current].slice(0, 25));
    });
    const fallback = window.setInterval(() => {
      void fetchRecentEvents(10)
        .then((recent) => setEvents(recent.slice().reverse()))
        .catch(() => setStreamState("polling"));
    }, 4000);
    return () => {
      dispose();
      window.clearInterval(fallback);
    };
  }, []);

  if (health.isLoading) {
    return <main className="shell"><p className="muted">Contacting the daemon…</p></main>;
  }

  if (health.isError) {
    return (
      <main className="shell">
        <h1>Meta-Harness</h1>
        <section className="panel error">
          <h2>Daemon unreachable</h2>
          <p>{(health.error as Error).message}</p>
          <p className="muted">
            The UI is served by the daemon; if you opened this file directly there is no API to
            talk to. Start it with <code>python scripts/dev.py</code>.
          </p>
        </section>
      </main>
    );
  }

  const data = health.data!;
  const info = version.data;

  return (
    <main className="shell">
      <header>
        <h1>Meta-Harness</h1>
        <p className="muted">
          v2 control plane — WP-002 skeleton. This page exists to prove the wiring; there are no
          agents, missions or tasks yet.
        </p>
      </header>

      <section className="panel">
        <h2>
          daemon <span className={data.status === "ok" ? "ok" : "warn"}>{data.status}</span>
        </h2>
        <dl>
          <dt>version</dt>
          <dd>
            {data.version} · {data.git_sha}
            {info ? ` · ${info.git_branch}` : ""}
          </dd>
          <dt>uptime</dt>
          <dd>{data.uptime_s.toFixed(1)} s</dd>
          <dt>data root</dt>
          <dd>
            <code>{data.data_root}</code>
          </dd>
          <dt>runtime</dt>
          <dd>{info ? `${info.python} · ${info.platform}` : "…"}</dd>
          <dt>web bundle</dt>
          <dd>{data.web_bundle ?? "not built — API-only mode"}</dd>
          <dt>events</dt>
          <dd>
            last_seq {data.events.last_seq} · {data.events.subscribers} subscriber(s) · stream{" "}
            <span className={streamState === "live" ? "ok" : "warn"}>{streamState}</span>
          </dd>
        </dl>
      </section>

      <section className="panel">
        <h2>recent events</h2>
        {events.length === 0 ? (
          <p className="muted">no events received yet</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>seq</th>
                <th>kind</th>
                <th>provenance</th>
                <th>payload</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td>{event.seq}</td>
                  <td>{event.kind}</td>
                  <td>{event.provenance.method}</td>
                  <td>
                    <code>{JSON.stringify(event.payload)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
