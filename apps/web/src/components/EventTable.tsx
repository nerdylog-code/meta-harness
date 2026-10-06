/**
 * The event inspector's table.
 *
 * It renders the canonical envelope as it is: the 14 frozen keys, with the payload shown as
 * a one-line JSON summary and the provenance label verbatim. Nothing is prettified into a
 * friendlier shape, because the point of this view is to see what the daemon actually wrote.
 */

import { EventEnvelope, formatTimestamp, provenanceLabel, shortId } from "../api";

function payloadSummary(payload: Record<string, unknown>, width = 110): string {
  const json = JSON.stringify(payload);
  if (!json) return "—";
  return json.length > width ? `${json.slice(0, width)}…` : json;
}

export function EventTable({ events, showIds = true }: { events: EventEnvelope[]; showIds?: boolean }) {
  if (events.length === 0) {
    return (
      <div className="empty">
        <strong>no events yet</strong>
        Nothing has been recorded since this daemon started. Events appear here as the daemon
        emits them, and the durable backlog is replayed on connect.
      </div>
    );
  }

  return (
    <table>
      <thead>
        <tr>
          <th>seq</th>
          <th>time (utc)</th>
          <th>kind</th>
          <th>provenance</th>
          {showIds ? <th>agent / session / run</th> : null}
          <th>payload</th>
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <tr key={`${event.seq}-${event.id}`}>
            <td className="num">{event.seq}</td>
            <td className="mono faint">{formatTimestamp(event.ts)}</td>
            <td className="kind">{event.kind}</td>
            <td className={provenanceLabel(event) === "measured" ? "ok" : "warn"}>
              {provenanceLabel(event)}
            </td>
            {showIds ? (
              <td className="mono faint">
                {shortId(event.agent_id)} · {shortId(event.session_id)} · {shortId(event.run_id)}
              </td>
            ) : null}
            <td>
              <span className="payload mono">{payloadSummary(event.payload)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
