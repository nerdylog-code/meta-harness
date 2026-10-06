/**
 * Agent Inspector: where Nova runs, and the one operation that changes it (M2).
 *
 * The modal states the consequences before they happen, because a migration is not a settings
 * toggle: the agent stays the same, a **new session** is created on the target runtime, the
 * previous session is **archived** (not deleted -- its events stay), and the state travels as a
 * **verified Context Capsule**. Nothing here decides any of that; the daemon does, and this panel
 * reports what the daemon recorded, including a migration that failed partway.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AgentRow,
  RuntimesPayload,
  fetchCapsules,
  fetchMigrations,
  fetchRuntimes,
  migrateAgent,
} from "../api";
import { EmptyState, KeyValues, Loading, Panel } from "./Panel";

function MigrationModal({
  agent,
  runtimes,
  onClose,
}: {
  agent: AgentRow;
  runtimes: RuntimesPayload["runtimes"];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const current = agent.versions.at(-1)?.runtime_preferred ?? null;
  const options = runtimes.filter((row) => row.runtime_id !== current);
  const [target, setTarget] = useState(options[0]?.runtime_id ?? "");
  const chosen = runtimes.find((row) => row.runtime_id === target);

  const migrate = useMutation({
    mutationFn: () => migrateAgent(agent.id, target),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      await queryClient.invalidateQueries({ queryKey: ["sessions", agent.id] });
      await queryClient.invalidateQueries({ queryKey: ["migrations", agent.id] });
      await queryClient.invalidateQueries({ queryKey: ["capsules", agent.id] });
      onClose();
    },
  });

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Move runtime">
      <div className="modal">
        <h2>Move {agent.display_name} to another runtime</h2>
        <p className="muted">
          This does not create a new agent. It adds a <strong>version</strong> to{" "}
          <span className="mono">{agent.id}</span> pointing at another runtime.
        </p>
        <KeyValues
          rows={[
            ["agent", <span className="mono">{agent.id}</span>],
            ["current runtime", <span className="mono">{current ?? "—"}</span>],
            ["target runtime", <span className="mono">{target || "—"}</span>],
            [
              "target available",
              chosen ? (
                chosen.available ? (
                  <span className="ok">yes · {chosen.detail}</span>
                ) : (
                  <span className="error">no · {chosen.detail}</span>
                )
              ) : (
                "—"
              ),
            ],
          ]}
        />
        <ul className="facts">
          <li>the agent keeps the same id and the same mission</li>
          <li>a <strong>new session</strong> is created on the target runtime</li>
          <li>the previous session is <strong>archived</strong> — its events stay in the log</li>
          <li>the state travels as a <strong>verified Context Capsule</strong>, not as chat history</li>
          <li>
            if the target is unavailable, <strong>nothing moves</strong> and the current session
            stays exactly as it is
          </li>
        </ul>
        <div className="toolbar">
          <select aria-label="target runtime" value={target} onChange={(event) => setTarget(event.target.value)}>
            {options.length === 0 ? <option value="">no other runtime configured</option> : null}
            {options.map((row) => (
              <option key={row.runtime_id} value={row.runtime_id}>
                {row.runtime_id} ({row.name ?? "?"})
              </option>
            ))}
          </select>
          <button type="button" onClick={() => migrate.mutate()} disabled={!target || migrate.isPending}>
            move runtime
          </button>
          <button type="button" className="ghost" onClick={onClose}>
            cancel
          </button>
        </div>
        {migrate.isError ? (
          <p className="error tight">
            {(migrate.error as Error).message}
            <br />
            <span className="faint">
              a failed migration is recorded with the stage it reached: see the lifecycle below.
            </span>
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function RuntimeMigrationPanel({ agent }: { agent: AgentRow }) {
  const [open, setOpen] = useState(false);
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: fetchRuntimes, refetchInterval: 10000 });
  const migrations = useQuery({
    queryKey: ["migrations", agent.id],
    queryFn: () => fetchMigrations(agent.id),
    refetchInterval: 5000,
  });
  const capsules = useQuery({
    queryKey: ["capsules", agent.id],
    queryFn: () => fetchCapsules(agent.id),
    refetchInterval: 10000,
  });

  const latest = agent.versions.at(-1);
  const rows = migrations.data?.migrations ?? [];
  const capsuleRows = capsules.data?.capsules ?? [];
  const lastCapsule = capsuleRows[0];

  return (
    <>
      <Panel
        title="agent inspector"
        hint={`${agent.versions.length} version${agent.versions.length === 1 ? "" : "s"}`}
      >
        <KeyValues
          rows={[
            ["identity", <span className="mono">{agent.id}</span>],
            ["runtime policy", <span className="mono">{latest?.runtime_preferred ?? "—"}</span>],
            ["model policy", <span className="mono">{latest?.model_primary ?? "—"}</span>],
            [
              "runtime availability",
              runtimes.data ? (
                (() => {
                  const row = runtimes.data.runtimes.find((item) => item.runtime_id === latest?.runtime_preferred);
                  if (!row) return <span className="faint">unknown runtime</span>;
                  return row.available ? (
                    <span className="ok">available · {row.protocol}</span>
                  ) : (
                    <span className="error">unavailable · {row.detail}</span>
                  );
                })()
              ) : (
                <Loading />
              ),
            ],
            [
              "state travels as",
              lastCapsule ? (
                <span className="mono">
                  {lastCapsule.id} {lastCapsule.verified ? <span className="ok">verified</span> : <span className="error">unverified</span>}
                </span>
              ) : (
                <span className="faint">no capsule yet</span>
              ),
            ],
          ]}
        />
        <div className="toolbar">
          <button type="button" onClick={() => setOpen(true)} disabled={!runtimes.data}>
            move runtime…
          </button>
        </div>
      </Panel>

      <Panel title="migrations" hint="lifecycle, including the ones that failed">
        {rows.length === 0 ? (
          <EmptyState title="no migrations">
            This agent has never changed runtime. When it does, every stage is recorded here —
            including a partial attempt, which is a fact and not something to hide.
          </EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>when</th>
                <th>from → to</th>
                <th>state</th>
                <th>capsule</th>
                <th>sessions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="faint">{new Date(row.created_ts * 1000).toLocaleString()}</td>
                  <td className="mono">
                    {row.from_runtime ?? "—"} → {row.to_runtime ?? "—"}
                  </td>
                  <td className={row.state.startsWith("failed") ? "error" : "ok"}>
                    {row.state}
                    {row.failed_stage ? <div className="faint">stopped at {row.failed_stage}</div> : null}
                  </td>
                  <td className="mono">{row.capsule_id ?? "—"}</td>
                  <td className="mono faint">
                    {row.from_session ?? "—"} → {row.to_session ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {open && runtimes.data ? (
        <MigrationModal agent={agent} runtimes={runtimes.data.runtimes} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}
