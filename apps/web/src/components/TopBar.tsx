/**
 * The topbar: daemon identity, the connection state, and the counters that are real today.
 *
 * Every number here comes from `/health` or from the socket. There is no cost, token or
 * heartbeat figure, because the daemon does not measure those yet -- inventing a `$0.00`
 * would be exactly the fake green the Book forbids (BOOK §82). They appear when usage
 * samples do.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "../api";
import { useStream } from "../stream";

function ConnectionBadge() {
  const stream = useStream();
  const label =
    stream.state === "live" ? "live" : stream.state === "connecting" ? "connecting" : "degraded";
  return (
    <span className={`badge ${stream.state === "live" ? "live" : stream.state === "connecting" ? "degraded" : "offline"}`}>
      {label}
      {stream.state === "degraded" && stream.reconnectAttempts > 0
        ? ` · retry ${stream.reconnectAttempts}`
        : ""}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
    </div>
  );
}

export function TopBar() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 5000 });

  return (
    <header className="topbar">
      <Stat label="daemon" value={health.data ? `v${health.data.version}` : health.isError ? "unreachable" : "…"} />
      <Stat label="events" value={health.data ? String(health.data.store.events ?? 0) : "—"} />
      <Stat label="schema" value={health.data?.store.schema_version ? `v${health.data.store.schema_version}` : "—"} />
      <Stat label="subscribers" value={health.data ? String(health.data.events.subscribers) : "—"} />
      <Stat label="uptime" value={health.data ? `${Math.round(health.data.uptime_s)}s` : "—"} />
      <div className="spacer" />
      <ConnectionBadge />
    </header>
  );
}
