/**
 * The mission surface: three projections of one daemon-owned work graph.
 *
 * What this page is and is not:
 *
 * * **the daemon owns the graph.** Everything shown comes from `GET /v1/missions/ID/tasks`
 *   and is refetched on an interval. This page keeps no task state of its own and never moves
 *   a task optimistically: after an action it invalidates the query and renders what came back.
 * * **the eight states are shown by name.** draft, ready, running, review, blocked, done,
 *   failed and cancelled are distinct; folding them into "in progress" or "done" is how a UI
 *   starts lying about what actually happened.
 * * **a refusal is a refusal.** A 409 from the daemon (a cycle, an unmet dependency, an
 *   illegal transition) is rendered with the daemon's own detail string, never swallowed.
 * * **`start` is enabled only when the daemon says the task is ready** -- never on a guess.
 */

import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRefusal,
  MissionTask,
  addTaskDependency,
  assignTask,
  cancelTask,
  completeTask,
  createTask,
  failTask,
  fetchMissionTasks,
  fetchMissions,
  removeTaskDependency,
  startTask,
} from "../api";
import { EmptyState, Failure, KeyValues, Loading, Panel } from "../components/Panel";

//: Every state the daemon can put a task in, in the order work flows through them. The UI
//: renders the exact word and never folds two of them together.
const TASK_STATES = [
  "draft",
  "ready",
  "running",
  "review",
  "blocked",
  "done",
  "failed",
  "cancelled",
] as const;

type View = "overview" | "graph" | "tasks";

const VIEWS: View[] = ["overview", "graph", "tasks"];

//: Stable empty array so `useMemo` dependencies do not churn while the query is loading.
const EMPTY_TASKS: MissionTask[] = [];

interface Notice {
  kind: "ok" | "refusal" | "error";
  text: string;
}

function noticeFromError(error: unknown): Notice {
  if (error instanceof ApiRefusal) {
    return { kind: "refusal", text: `${error.status} refused: ${error.detail}` };
  }
  return { kind: "error", text: error instanceof Error ? error.message : String(error) };
}

/** The badge class for a state on its own, used where there is no task (the counts table). */
function stateClass(state: string): string {
  switch (state) {
    case "running":
    case "review":
      return "badge badge-moderate";
    case "blocked":
    case "failed":
      return "badge badge-weak";
    case "done":
      return "badge badge-strong";
    default:
      return "badge badge-unknown";
  }
}

/** The badge class for a task: the daemon's `ready` boolean always wins as strong. */
function stateBadge(task: MissionTask): string {
  return task.ready ? "badge badge-strong" : stateClass(task.state);
}

/**
 * Which actions are legal for a task right now. The milestone's hard rule lives here: `start`
 * is enabled only when the daemon says the task is ready. `complete` belongs to a running
 * task; `fail` and `cancel` are the escape hatches for everything not yet terminal.
 */
function legality(task: MissionTask): {
  start: boolean;
  complete: boolean;
  fail: boolean;
  cancel: boolean;
} {
  const terminal = task.state === "done" || task.state === "failed" || task.state === "cancelled";
  return {
    start: task.ready,
    complete: task.state === "running",
    fail: !terminal && task.state !== "running",
    cancel: !terminal,
  };
}

/**
 * Waves: wave 0 is the set of tasks whose dependencies are all done; wave n depends only on
 * waves before it. Anything never placed (an unmet dependency or a cycle) is returned
 * separately rather than dropped.
 */
function computeWaves(tasks: MissionTask[]): { waves: MissionTask[][]; unplaced: MissionTask[] } {
  const placed = new Set(tasks.filter((task) => task.state === "done").map((task) => task.id));
  const pending = tasks.filter((task) => !placed.has(task.id));
  const waves: MissionTask[][] = [];
  let progressed = true;
  while (progressed) {
    progressed = false;
    const wave = pending.filter(
      (task) => !placed.has(task.id) && task.dependencies.every((dep) => placed.has(dep)),
    );
    if (wave.length > 0) {
      waves.push(wave);
      for (const task of wave) placed.add(task.id);
      progressed = true;
    }
  }
  const unplaced = pending.filter((task) => !placed.has(task.id));
  return { waves, unplaced };
}

function titleOf(tasks: MissionTask[], id: string): string {
  return tasks.find((task) => task.id === id)?.title ?? id;
}

function ArtifactReferences({ value, label }: { value: string; label: string }) {
  let entries: unknown = value;
  try { entries = JSON.parse(value) as unknown; } catch { /* retain the original plain text */ }
  const values = Array.isArray(entries) ? entries : [entries];
  return <div className="artifact-references">{label}: {values.map((entry, index) => {
    const text = typeof entry === "string" ? entry : JSON.stringify(entry) ?? String(entry);
    return <span key={`${index}-${text}`}>{index > 0 ? ", " : ""}{text.startsWith("art_") ? <Link to="/artifacts/$artifactId" params={{ artifactId: text }}>{text}</Link> : text}</span>;
  })}</div>;
}

export function MissionPage({ missionId }: { missionId: string }) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>("overview");
  const [notice, setNotice] = useState<Notice | null>(null);

  // create-task form state
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [deps, setDeps] = useState<string[]>([]);
  const [requiresArtifact, setRequiresArtifact] = useState(false);
  const [ownerAgent, setOwnerAgent] = useState("");

  // per-row form state, keyed by task id
  const [proof, setProof] = useState<Record<string, string>>({});
  const [reason, setReason] = useState<Record<string, string>>({});
  const [assign, setAssign] = useState<Record<string, string>>({});
  const [depChoice, setDepChoice] = useState<Record<string, string>>({});

  const missions = useQuery({ queryKey: ["missions"], queryFn: fetchMissions, refetchInterval: 8000 });
  const graph = useQuery({
    queryKey: ["mission-tasks", missionId],
    queryFn: () => fetchMissionTasks(missionId),
    refetchInterval: 4000,
  });

  const tasks = graph.data?.tasks ?? EMPTY_TASKS;

  const counts = useMemo(() => {
    const map = new Map<string, MissionTask[]>();
    for (const state of TASK_STATES) map.set(state, []);
    for (const task of tasks) {
      const bucket = map.get(task.state);
      if (bucket) bucket.push(task);
      else map.set(task.state, [task]);
    }
    return map;
  }, [tasks]);

  const waves = useMemo(() => computeWaves(tasks), [tasks]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["mission-tasks", missionId] });
  const onOk = (message: string) => (data: MissionTask) => {
    setNotice({ kind: "ok", text: `${message}: ${data.id} is ${data.state}` });
    void refresh();
  };
  const onErr = (error: unknown) => setNotice(noticeFromError(error));

  const create = useMutation({
    mutationFn: () =>
      createTask(missionId, {
        title,
        description: objective,
        dependencies: deps,
        requiresArtifact,
        ownerAgent: ownerAgent.trim() || null,
      }),
    onSuccess: (data) => {
      setNotice({ kind: "ok", text: `created ${data.id} (${data.state})` });
      setTitle("");
      setObjective("");
      setDeps([]);
      setRequiresArtifact(false);
      setOwnerAgent("");
      void refresh();
    },
    onError: onErr,
  });

  const start = useMutation({
    mutationFn: (taskId: string) => startTask(taskId),
    onSuccess: onOk("started"),
    onError: onErr,
  });
  const complete = useMutation({
    mutationFn: (input: { taskId: string; proof: string[] }) => completeTask(input.taskId, input.proof),
    onSuccess: onOk("completed"),
    onError: onErr,
  });
  const fail = useMutation({
    mutationFn: (input: { taskId: string; reason: string | null }) => failTask(input.taskId, input.reason),
    onSuccess: onOk("failed"),
    onError: onErr,
  });
  const cancel = useMutation({
    mutationFn: (input: { taskId: string; reason: string | null }) => cancelTask(input.taskId, input.reason),
    onSuccess: onOk("cancelled"),
    onError: onErr,
  });
  const assignTo = useMutation({
    mutationFn: (input: { taskId: string; agentId: string }) => assignTask(input.taskId, input.agentId),
    onSuccess: onOk("assigned"),
    onError: onErr,
  });
  const addDep = useMutation({
    mutationFn: (input: { taskId: string; dependsOn: string }) =>
      addTaskDependency(input.taskId, input.dependsOn),
    onSuccess: onOk("dependency added"),
    onError: onErr,
  });
  const removeDep = useMutation({
    mutationFn: (input: { taskId: string; dependencyId: string }) =>
      removeTaskDependency(input.taskId, input.dependencyId),
    onSuccess: onOk("dependency removed"),
    onError: onErr,
  });

  if (graph.isLoading) return <Loading label="reading the work graph" />;
  if (graph.isError) return <Failure label="mission tasks" error={graph.error} />;
  const payload = graph.data;
  if (!payload) return <Failure label="mission tasks" error="the daemon returned no payload" />;

  const mission = missions.data?.missions.find((row) => row.id === missionId) ?? null;
  const patch = <T,>(record: Record<string, T>, key: string, value: T): Record<string, T> => ({
    ...record,
    [key]: value,
  });

  return (
    <>
      <Panel title={mission?.title ?? "mission"} hint={missionId}>
        <KeyValues
          rows={[
            ["objective", mission?.objective ? mission.objective : <span className="faint">—</span>],
            ["status", <span className="badge badge-moderate">{mission?.status ?? "unknown"}</span>],
            ["owner", <span className="mono">{mission?.owner ?? "—"}</span>],
            ["tasks", String(tasks.length)],
          ]}
        />
      </Panel>

      {notice ? (
        <p className={notice.kind === "ok" ? "tight ok" : "tight error"}>
          {notice.kind === "refusal" ? "refused by the daemon — " : ""}
          {notice.text}
        </p>
      ) : null}

      <div className="tabs" role="tablist" aria-label="mission views">
        {VIEWS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={view === name}
            onClick={() => setView(name)}
          >
            {name}
          </button>
        ))}
      </div>

      {view === "overview" ? (
        <>
          <Panel title="states" hint="one row per state, counted from the daemon's tasks">
            <table>
              <thead>
                <tr>
                  <th>state</th>
                  <th>count</th>
                  <th>tasks</th>
                </tr>
              </thead>
              <tbody>
                {TASK_STATES.map((state) => {
                  const bucket = counts.get(state) ?? [];
                  return (
                    <tr key={state}>
                      <td>
                        <span className={stateClass(state)}>{state}</span>
                      </td>
                      <td className="num">{bucket.length}</td>
                      <td className="faint">{bucket.map((task) => task.title).join(" · ") || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>

          <div className="grid">
            <Panel title="ready now" hint={`${payload.ready.length} tasks`}>
              {payload.ready.length === 0 ? (
                <p className="tight faint">no task is ready</p>
              ) : (
                <ul className="plain">
                  {payload.ready.map((id) => (
                    <li key={id}>
                      {titleOf(tasks, id)} <span className="mono faint">{id}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="blocked now" hint={`${payload.blocked.length} tasks`}>
              {payload.blocked.length === 0 ? (
                <p className="tight faint">no task is blocked</p>
              ) : (
                <ul className="plain">
                  {payload.blocked.map((id) => (
                    <li key={id}>
                      {titleOf(tasks, id)} <span className="mono faint">{id}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      ) : null}

      {view === "graph" ? (
        <>
          <Panel title="waves" hint="wave 0 = every dependency already done">
            {waves.waves.length === 0 ? (
              <EmptyState title="no waves">
                Every task is either done or waiting on something unplaced, so nothing forms a wave.
              </EmptyState>
            ) : (
              waves.waves.map((wave, index) => (
                <div className="wave" key={index}>
                  <h3>wave {index}</h3>
                  <ul className="plain">
                    {wave.map((task) => (
                      <li key={task.id}>
                        <span className={stateBadge(task)}>{task.state}</span> {task.title}
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
            {waves.unplaced.length > 0 ? (
              <p className="tight warn">
                unplaced (unmet dependency or a cycle):{" "}
                {waves.unplaced.map((task) => task.title).join(", ")}
              </p>
            ) : null}
          </Panel>

          <Panel title="dependency structure" hint="dependencies and dependents by title">
            {tasks.length === 0 ? (
              <EmptyState title="no tasks">The graph is empty. Create a task on the Tasks view.</EmptyState>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>task</th>
                    <th>state</th>
                    <th>depends on</th>
                    <th>blocks</th>
                    <th>add dependency</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((task) => (
                    <tr key={task.id}>
                      <td>
                        {task.title}
                        <div className="faint mono">{task.id}</div>
                      </td>
                      <td>
                        <span className={stateBadge(task)}>{task.state}</span>
                        {task.ready ? (
                          <>
                            {" "}
                            <span className="badge badge-strong">ready</span>
                          </>
                        ) : null}
                      </td>
                      <td>
                        {task.dependencies.length === 0 ? (
                          <span className="faint">—</span>
                        ) : (
                          <ul className="plain">
                            {task.dependencies.map((id) => (
                              <li key={id}>
                                {titleOf(tasks, id)}{" "}
                                <button
                                  type="button"
                                  onClick={() => removeDep.mutate({ taskId: task.id, dependencyId: id })}
                                  disabled={removeDep.isPending}
                                >
                                  remove
                                </button>
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td>
                        {task.dependents.length === 0 ? (
                          <span className="faint">—</span>
                        ) : (
                          <ul className="plain">
                            {task.dependents.map((id) => (
                              <li key={id}>{titleOf(tasks, id)}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td>
                        <div className="task-actions">
                          <select
                            aria-label={`add dependency to ${task.title}`}
                            value={depChoice[task.id] ?? ""}
                            onChange={(event) => setDepChoice(patch(depChoice, task.id, event.target.value))}
                          >
                            <option value="">choose task…</option>
                            {tasks
                              .filter((other) => other.id !== task.id && !task.dependencies.includes(other.id))
                              .map((other) => (
                                <option key={other.id} value={other.id}>
                                  {other.title}
                                </option>
                              ))}
                          </select>
                          <button
                            type="button"
                            disabled={!(depChoice[task.id] ?? "") || addDep.isPending}
                            onClick={() =>
                              addDep.mutate({ taskId: task.id, dependsOn: depChoice[task.id] ?? "" })
                            }
                          >
                            add
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      ) : null}

      {view === "tasks" ? (
        <>
          <Panel title="new task" hint="the daemon assigns the id and the initial state">
            <div className="toolbar">
              <input
                aria-label="task title"
                placeholder="title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <input
                aria-label="task objective"
                placeholder="objective"
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
              />
              <input
                aria-label="owner agent"
                placeholder="owner agent (blank = none)"
                value={ownerAgent}
                onChange={(event) => setOwnerAgent(event.target.value)}
              />
            </div>
            <fieldset className="checks">
              <legend>dependencies</legend>
              {tasks.length === 0 ? (
                <span className="faint">no existing tasks to depend on</span>
              ) : (
                tasks.map((task) => (
                  <label className="check" key={task.id}>
                    <input
                      type="checkbox"
                      checked={deps.includes(task.id)}
                      onChange={(event) =>
                        setDeps(
                          event.target.checked
                            ? [...deps, task.id]
                            : deps.filter((id) => id !== task.id),
                        )
                      }
                    />
                    {task.title}
                  </label>
                ))
              )}
            </fieldset>
            <div className="toolbar">
              <label className="check">
                <input
                  type="checkbox"
                  checked={requiresArtifact}
                  onChange={(event) => setRequiresArtifact(event.target.checked)}
                />
                requires artifact
              </label>
              <button
                type="button"
                disabled={!title.trim() || create.isPending}
                onClick={() => create.mutate()}
              >
                create task
              </button>
            </div>
          </Panel>

          <Panel title="tasks" hint={`${tasks.length} tasks`}>
            {tasks.length === 0 ? (
              <EmptyState title="no tasks yet">
                Create one above; it will appear here on the next read from the daemon.
              </EmptyState>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>title</th>
                    <th>state</th>
                    <th>assigned agent</th>
                    <th>run id</th>
                    <th>deps</th>
                    <th>actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((task) => {
                    const legal = legality(task);
                    return (
                      <tr key={task.id}>
                        <td>
                          {task.title}
                          <div className="faint mono">{task.id}</div>
                          {task.proof ? <ArtifactReferences value={task.proof} label="proof" /> : null}
                          {task.artifacts ? <ArtifactReferences value={task.artifacts} label="artifacts" /> : null}
                        </td>
                        <td>
                          <span className={stateBadge(task)}>{task.state}</span>
                          {task.ready ? (
                            <>
                              {" "}
                              <span className="badge badge-strong">ready</span>
                            </>
                          ) : null}
                        </td>
                        <td>
                          <span className="mono">
                            {task.owner_agent ?? "—"}
                          </span>
                          <div className="task-actions">
                            <input
                              aria-label={`assign agent to ${task.title}`}
                              placeholder="agent id"
                              value={assign[task.id] ?? ""}
                              onChange={(event) => setAssign(patch(assign, task.id, event.target.value))}
                            />
                            <button
                              type="button"
                              disabled={!(assign[task.id] ?? "").trim() || assignTo.isPending}
                              onClick={() =>
                                assignTo.mutate({
                                  taskId: task.id,
                                  agentId: (assign[task.id] ?? "").trim(),
                                })
                              }
                            >
                              assign
                            </button>
                          </div>
                        </td>
                        <td className="mono">{task.run_id ?? "—"}</td>
                        <td className="num">{task.dependencies.length}</td>
                        <td>
                          <div className="task-actions">
                            <button
                              type="button"
                              disabled={!legal.start || start.isPending}
                              onClick={() => start.mutate(task.id)}
                            >
                              start
                            </button>
                            <input
                              aria-label={`proof for ${task.title}`}
                              placeholder="proof, one per line"
                              value={proof[task.id] ?? ""}
                              onChange={(event) => setProof(patch(proof, task.id, event.target.value))}
                            />
                            <button
                              type="button"
                              disabled={!legal.complete || complete.isPending}
                              onClick={() =>
                                complete.mutate({
                                  taskId: task.id,
                                  proof: (proof[task.id] ?? "")
                                    .split("\n")
                                    .map((line) => line.trim())
                                    .filter(Boolean),
                                })
                              }
                            >
                              complete
                            </button>
                            <input
                              aria-label={`reason for ${task.title}`}
                              placeholder="reason"
                              value={reason[task.id] ?? ""}
                              onChange={(event) => setReason(patch(reason, task.id, event.target.value))}
                            />
                            <button
                              type="button"
                              disabled={!legal.fail || fail.isPending}
                              onClick={() =>
                                fail.mutate({
                                  taskId: task.id,
                                  reason: (reason[task.id] ?? "").trim() || null,
                                })
                              }
                            >
                              fail
                            </button>
                            <button
                              type="button"
                              disabled={!legal.cancel || cancel.isPending}
                              onClick={() =>
                                cancel.mutate({
                                  taskId: task.id,
                                  reason: (reason[task.id] ?? "").trim() || null,
                                })
                              }
                            >
                              cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      ) : null}
    </>
  );
}
