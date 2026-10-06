/**
 * Event inspector: the canonical log, as it arrives.
 *
 * Filtering happens in the browser over the frames the daemon sent, and the view always says
 * which source it is showing -- the live socket or the durable backlog read over HTTP. That
 * distinction is the difference between "nothing is happening" and "the socket is down", and
 * a UI that blurs it is lying about the system's state.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { EventEnvelope, fetchRecentEvents, namespaceOf } from "../api";
import { EventTable } from "../components/EventTable";
import { Panel } from "../components/Panel";
import { useStream } from "../stream";

const LIMITS = [25, 50, 100, 250];

export function EventInspectorPage() {
  const stream = useStream();
  const [kindFilter, setKindFilter] = useState("");
  const [namespaceFilter, setNamespaceFilter] = useState("");
  const [limit, setLimit] = useState(50);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<EventEnvelope[]>([]);

  const backlog = useQuery({
    queryKey: ["events", limit],
    queryFn: () => fetchRecentEvents(limit),
    // While the socket is live it is the source; when it is not, the backlog is polled so
    // the page never shows a silent empty table.
    refetchInterval: stream.state === "live" ? false : 3000,
  });

  const live = stream.state === "live" && stream.events.length > 0;
  const source: EventEnvelope[] = paused
    ? frozen
    : live
      ? stream.events.slice(-limit)
      : (backlog.data ?? []);

  const namespaces = useMemo(() => {
    const found = new Set<string>();
    for (const event of source) found.add(namespaceOf(event.kind));
    return [...found].sort();
  }, [source]);

  const filtered = useMemo(() => {
    const needle = kindFilter.trim().toLowerCase();
    return source.filter((event) => {
      if (needle && !event.kind.toLowerCase().includes(needle)) return false;
      if (namespaceFilter && namespaceOf(event.kind) !== namespaceFilter) return false;
      return true;
    });
  }, [source, kindFilter, namespaceFilter]);

  const ordered = useMemo(() => [...filtered].reverse(), [filtered]);

  return (
    <Panel
      title="event stream"
      hint={
        paused
          ? "paused"
          : live
            ? `live · ${stream.events.length} frames in buffer`
            : stream.state === "degraded"
              ? "socket degraded · reading the durable backlog"
              : "connecting"
      }
    >
      <div className="toolbar">
        <input
          aria-label="filter by kind"
          placeholder="filter kind (e.g. system.)"
          value={kindFilter}
          onChange={(event) => setKindFilter(event.target.value)}
        />
        <select
          aria-label="filter by namespace"
          value={namespaceFilter}
          onChange={(event) => setNamespaceFilter(event.target.value)}
        >
          <option value="">all namespaces</option>
          {namespaces.map((namespace) => (
            <option key={namespace} value={namespace}>
              {namespace}
            </option>
          ))}
        </select>
        <select
          aria-label="backlog size"
          value={limit}
          onChange={(event) => setLimit(Number(event.target.value))}
        >
          {LIMITS.map((value) => (
            <option key={value} value={value}>
              last {value}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            if (paused) {
              setPaused(false);
              setFrozen([]);
            } else {
              setFrozen(source);
              setPaused(true);
            }
          }}
        >
          {paused ? "resume" : "pause"}
        </button>
        <button type="button" onClick={stream.clear} disabled={paused}>
          clear buffer
        </button>
        <span className="faint">
          {filtered.length} of {source.length} shown
          {stream.state === "degraded" && stream.reconnectAttempts > 0
            ? ` · ${stream.reconnectAttempts} reconnect attempts`
            : ""}
        </span>
      </div>

      {stream.error ? <p className="tight error">socket: {stream.error}</p> : null}

      <EventTable events={ordered} />
    </Panel>
  );
}
