/**
 * The roster: real agents, or an honest statement that there are none (WP-019).
 *
 * Creating an agent here records an identity and its first configuration version. It does not
 * start anything and does not pretend to: a session is a separate, explicit action on the
 * agent's own page, because starting a process and spending provider credits is not something
 * a list should do as a side effect of a button labelled "create".
 */

import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAgent, fetchAgents, fetchRuntime } from "../api";
import { EmptyState, Failure, KeyValues, Loading, Panel } from "../components/Panel";

export function AgentsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("Nova");
  const [model, setModel] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const agents = useQuery({ queryKey: ["agents"], queryFn: fetchAgents, refetchInterval: 5000 });
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: fetchRuntime, refetchInterval: 5000 });

  const create = useMutation({
    mutationFn: () => createAgent(name, model || null),
    onSuccess: async (data) => {
      setNotice(`created ${name} as ${data.agent_id} (version 1)`);
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error: Error) => setNotice(`could not create: ${error.message}`),
  });

  if (agents.isLoading) return <Loading label="reading the roster" />;
  if (agents.isError) return <Failure label="agents" error={agents.error} />;

  const rows = agents.data?.agents ?? [];

  return (
    <>
      <Panel title="runtime" hint={runtime.data?.runtime.protocol ?? "…"}>
        {runtime.data ? (
          <KeyValues
            rows={[
              [
                "pi",
                runtime.data.runtime.available ? (
                  <span className="ok">available · {runtime.data.runtime.detail}</span>
                ) : (
                  <span className="error">unavailable · {runtime.data.runtime.detail}</span>
                ),
              ],
              [
                "declared unsupported",
                <span className="faint">
                  {Object.entries(runtime.data.runtime.capabilities.capabilities)
                    .filter(([, info]) => !info.supported)
                    .map(([key]) => key)
                    .slice(0, 6)
                    .join(" · ") || "none"}
                </span>,
              ],
              ["live sessions", String(runtime.data.sessions.length)],
            ]}
          />
        ) : (
          <Loading />
        )}
      </Panel>

      <Panel title="new agent" hint="R1 · recorded as events">
        <div className="toolbar">
          <input
            aria-label="display name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="display name"
          />
          <input
            aria-label="model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="model (blank = runtime default)"
          />
          <button type="button" onClick={() => create.mutate()} disabled={create.isPending || !name}>
            create agent
          </button>
        </div>
        {notice ? <p className="tight faint">{notice}</p> : null}
      </Panel>

      <Panel title="roster" hint={`${rows.length} identities`}>
        {rows.length === 0 ? (
          <EmptyState title="no agents yet">
            An agent is an identity with a versioned configuration. Create one above, then open it
            to start a real session on Pi. Until you do, there is nothing here to show — and
            nothing here is invented.
          </EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>agent</th>
                <th>role</th>
                <th>versions</th>
                <th>runtime policy</th>
                <th>model policy</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((agent) => {
                const latest = agent.versions.at(-1);
                return (
                  <tr key={agent.id}>
                    <td>
                      <Link to="/agents/$agentId" params={{ agentId: agent.id }}>
                        {agent.display_name}
                      </Link>
                      <div className="faint mono">{agent.id}</div>
                    </td>
                    <td>{agent.role}</td>
                    <td className="num">{agent.versions.length}</td>
                    <td className="mono">{latest?.runtime_preferred ?? "—"}</td>
                    <td className="mono">{latest?.model_primary ?? "—"}</td>
                    <td>
                      <Link to="/agents/$agentId" params={{ agentId: agent.id }}>
                        open
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
