/**
 * Agents: the roster shell.
 *
 * It exists now, before any agent can exist, so that the M1 slice has somewhere to land. What
 * it shows today is honest: agent identities seen in the log, or an explicit statement that
 * there are none. There is no "create agent" button, because the daemon has no endpoint that
 * could honour it.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchRecentEvents } from "../api";
import { EmptyState, KeyValues, Panel } from "../components/Panel";
import { useStream } from "../stream";

export function AgentsPage() {
  const stream = useStream();
  const backlog = useQuery({
    queryKey: ["events", "agents"],
    queryFn: () => fetchRecentEvents(250),
    refetchInterval: stream.state === "live" ? false : 4000,
  });

  const events = stream.state === "live" && stream.events.length > 0 ? stream.events : (backlog.data ?? []);

  const agents = useMemo(() => {
    const counts = new Map<string, number>();
    for (const event of events) {
      if (event.agent_id) counts.set(event.agent_id, (counts.get(event.agent_id) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [events]);

  return (
    <>
      <Panel title="agent roster" hint="derived from agent_id in the log">
        {agents.length === 0 ? (
          <EmptyState title="no agents yet">
            No event carries an <code>agent_id</code>. An agent identity is a persistent thing the
            daemon does not model yet: the roster, the agent registry and the Pi runtime adapter
            arrive together with the M1 slice (BOOK §75), which is what makes an agent
            <em> alive</em> rather than merely configured.
          </EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>agent</th>
                <th>events</th>
              </tr>
            </thead>
            <tbody>
              {agents.map(([agentId, count]) => (
                <tr key={agentId}>
                  <td className="mono">{agentId}</td>
                  <td className="num">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="what this page will become" hint="BOOK §61">
        <KeyValues
          rows={[
            ["identity", "name, role, runtime and model policy — versioned, so a mission records which version it used"],
            ["liveness", "status, current task, workspace and heartbeat — all measured, never assumed"],
            ["context", "budget pressure and cache behaviour, once usage samples exist"],
            ["actions", "chat, steer, pause, move runtime — each one an approval-classed operation"],
          ]}
        />
        <p className="tight faint">
          None of these are shown as empty gauges on purpose. A gauge that always reads zero is a
          claim that the number is measured.
        </p>
      </Panel>
    </>
  );
}
