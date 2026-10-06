/**
 * Missions: a projection of the log, not a second store.
 *
 * There is no missions table yet (that arrives with its own work package), so this page
 * derives what it can from the events it can see. When the registry lands, the page keeps
 * its shape and swaps the derivation for a query -- which is the point of deriving from the
 * log rather than inventing a client-side model now.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchRecentEvents } from "../api";
import { EmptyState, Panel } from "../components/Panel";
import { useStream } from "../stream";

function countBy(events: { mission_id: string | null }[], key: "mission_id"): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const event of events) {
    const value = event[key];
    if (value) counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

export function MissionsPage() {
  const stream = useStream();
  const backlog = useQuery({
    queryKey: ["events", "missions"],
    queryFn: () => fetchRecentEvents(250),
    refetchInterval: stream.state === "live" ? false : 4000,
  });

  const events = stream.state === "live" && stream.events.length > 0 ? stream.events : (backlog.data ?? []);
  const missions = useMemo(() => countBy(events, "mission_id"), [events]);

  return (
    <Panel title="missions" hint="projection of the event log">
      {missions.length === 0 ? (
        <EmptyState title="no missions yet">
          Nothing in the log carries a <code>mission_id</code>. Missions become real with the
          mission registry and the task graph (BOOK §78/§82); until then this page shows the truth
          rather than an empty table with invented columns.
        </EmptyState>
      ) : (
        <table>
          <thead>
            <tr>
              <th>mission</th>
              <th>events</th>
            </tr>
          </thead>
          <tbody>
            {missions.map(([missionId, count]) => (
              <tr key={missionId}>
                <td className="mono">{missionId}</td>
                <td className="num">{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="tight faint">
        Derived from {events.length} events in view. The daemon owns mission state; this page only
        counts what it was told.
      </p>
    </Panel>
  );
}
