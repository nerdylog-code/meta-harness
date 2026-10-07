import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ApiRefusal, WorkboardTask, cancelTask, failTask, fetchMissionBoard, formatTimestamp, requestTaskReview, startTask } from "../api";
import { EmptyState, Failure, Loading, Panel } from "../components/Panel";
import { useStream } from "../stream";

const RELEVANT_EVENT = /^(task|workspace|approval|artifact|run|usage)\./;

type FilterKey = "state" | "agent" | "runtime" | "workspace" | "lease" | "approval" | "blocked";

function errorText(error: unknown): string {
  if (error instanceof ApiRefusal) return `${error.status} refused: ${error.detail}`;
  return error instanceof Error ? error.message : String(error);
}

function stateClass(state: string): string {
  if (state === "done") return "badge badge-strong";
  if (state === "blocked" || state === "failed" || state === "cancelled") return "badge badge-weak";
  if (state === "running" || state === "review") return "badge badge-moderate";
  return "badge badge-unknown";
}

function matches(task: WorkboardTask, key: FilterKey, value: string): boolean {
  if (!value) return true;
  switch (key) {
    case "state": return task.state === value;
    case "agent": return task.agent?.id === value;
    case "runtime": return task.run?.runtime_id === value;
    case "workspace": return Boolean(task.workspace);
    case "lease": return Boolean(task.writer?.active && !task.writer.expired);
    case "approval": return task.approvals.pending > 0;
    case "blocked": return task.reasons.length > 0;
  }
}

function BoardCard({ task, missionId, onAction }: {
  task: WorkboardTask;
  missionId: string;
  onAction: (taskId: string, action: "start" | "review" | "fail" | "cancel") => void;
}) {
  const approval = task.approvals.latest;
  const workspace = task.workspace;
  const writer = task.writer;
  return <article className="workboard-card">
    <header className="workboard-card-title">
      <div><h3>{task.title}</h3><code className="mono">{task.task_id}</code></div>
      <span className={stateClass(task.state)}>{task.state}</span>
    </header>
    {task.agent ? <p className="workboard-line">agent: <a href={`/agents/${encodeURIComponent(task.agent.id)}`} className="mono">{task.agent.id}</a></p> : null}
    {task.run ? <p className="workboard-line">run: <a className="mono" href="/events">{task.run.run_id}</a>{task.run.runtime_id ? ` · runtime ${task.run.runtime_id}` : ""}{task.run.provider || task.run.model ? ` · ${[task.run.provider, task.run.model].filter(Boolean).join("/")}` : ""}{task.run.note ? ` · ${task.run.note}` : ""}</p> : null}
    {workspace ? <div className="workboard-line">workspace: <a href={`/missions/${encodeURIComponent(missionId)}?workspace=${encodeURIComponent(task.task_id)}`} className="mono workboard-locator">{workspace.locator}</a> · {workspace.provider} · {workspace.state}
      {(workspace.state === "missing" || workspace.state === "conflict") && workspace.note ? <p className="tight warn">{workspace.note}</p> : null}
      <p className="tight faint">dirty: {workspace.dirty === null ? "not measured" : workspace.dirty ? "yes" : "no"}</p>
    </div> : null}
    {writer ? <p className={`workboard-line ${writer.expired ? "warn" : ""}`}>writer: {writer.expired ? "EXPIRED" : writer.active ? "active" : writer.state} · <span className="mono">{writer.run_id}</span> · generation {writer.generation} · expires {formatTimestamp(writer.expires_at)}</p> : null}
    <p className="workboard-line">artifacts: {task.artifacts.ids.length > 0 ? <a href={`/artifacts/${encodeURIComponent(task.artifacts.ids[0])}`}>{task.artifacts.count} existing</a> : <span>{task.artifacts.count} existing</span>}{task.artifacts.recorded !== task.artifacts.count ? ` · ${task.artifacts.recorded} recorded` : ""} · {task.artifacts.proof} proof</p>
    {task.approvals.pending > 0 ? <p className="workboard-line"><a className="badge badge-moderate" href="/approvals">APPROVAL REQUIRED{approval ? ` · ${approval.id}` : ""}</a></p> : null}
    <p className="workboard-line">write isolation: <strong>{task.enforcement.write_isolation}</strong> · {task.enforcement.write_isolation_scope} · {task.enforcement.write_isolation_detail}</p>
    <p className="workboard-line">filesystem isolation: <strong>{task.enforcement.filesystem_isolation}</strong> · {task.enforcement.filesystem_isolation_scope} · {task.enforcement.filesystem_isolation_detail}</p>
    <p className="workboard-line faint">{task.enforcement.note}</p>
    {task.usage === null ? <p className="workboard-line faint">usage was not measured</p> : <p className="workboard-line faint">usage measured {formatTimestamp(task.usage.sampled_at)} · {JSON.stringify(task.usage.sample)}</p>}
    {task.reasons.length ? <ul className="workboard-reasons">{task.reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul> : null}
    <div className="workboard-actions">
      <a href={`/missions/${encodeURIComponent(missionId)}?task=${encodeURIComponent(task.task_id)}`}>open task</a>
      {task.agent ? <a href={`/agents/${encodeURIComponent(task.agent.id)}`}>open agent</a> : null}
      {workspace ? <a href={`/missions/${encodeURIComponent(missionId)}?workspace=${encodeURIComponent(task.task_id)}`}>open workspace</a> : null}
      {task.artifacts.ids.length > 0 ? <a href={`/artifacts/${encodeURIComponent(task.artifacts.ids[0])}`}>open artifacts</a> : null}
      {task.approvals.pending > 0 ? <a href="/approvals">open approvals</a> : null}
      {task.run ? <a href="/events">inspect run</a> : null}
      {task.state === "ready" ? <button type="button" onClick={() => onAction(task.task_id, "start")}>start</button> : null}
      {task.state === "running" ? <button type="button" onClick={() => onAction(task.task_id, "review")}>request review</button> : null}
      {!(["done", "failed", "cancelled"].includes(task.state)) ? <><button type="button" onClick={() => onAction(task.task_id, "fail")}>fail</button><button type="button" onClick={() => onAction(task.task_id, "cancel")}>cancel</button></> : null}
    </div>
  </article>;
}

export function WorkboardPage({ missionId }: { missionId: string }) {
  const stream = useStream();
  const query = useQuery({ queryKey: ["mission-board", missionId], queryFn: () => fetchMissionBoard(missionId) });
  const [filters, setFilters] = useState<Record<FilterKey, string>>({ state: "", agent: "", runtime: "", workspace: "", lease: "", approval: "", blocked: "" });
  const [notice, setNotice] = useState<string | null>(null);
  const lastSeq = useRef(0);
  const debounce = useRef<number | null>(null);

  useEffect(() => {
    const incoming = stream.events.filter((event) => event.seq > lastSeq.current);
    lastSeq.current = Math.max(lastSeq.current, stream.lastSeq);
    if (!incoming.some((event) => RELEVANT_EVENT.test(event.kind))) return;
    if (debounce.current !== null) window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => { void query.refetch(); }, 250);
    return () => { if (debounce.current !== null) window.clearTimeout(debounce.current); };
  }, [stream.events, stream.lastSeq, query.refetch]);

  const doAction = async (taskId: string, action: "start" | "review" | "fail" | "cancel") => {
    setNotice(null);
    try {
      if (action === "start") await startTask(taskId);
      else if (action === "review") await requestTaskReview(taskId);
      else if (action === "fail") await failTask(taskId);
      else await cancelTask(taskId);
      setNotice(`daemon accepted ${action} for ${taskId}`);
      await query.refetch();
    } catch (error) {
      setNotice(errorText(error));
      await query.refetch();
    }
  };

  const payload = query.data;
  const visibleByLane = useMemo(() => payload?.columns.map((column) => ({
    ...column,
    tasks: column.tasks.filter((task) => (Object.keys(filters) as FilterKey[]).every((key) => matches(task, key, filters[key]))),
  })) ?? [], [payload, filters]);

  if (query.isLoading) return <Loading label="reading the canonical workboard" />;
  if (query.isError) return <Failure label="workboard" error={query.error} />;
  if (!payload) return <Failure label="workboard" error="the daemon returned no payload" />;

  const setFilter = (key: FilterKey, value: string) => setFilters((current) => ({ ...current, [key]: value }));
  const filterLabel = (Object.keys(filters) as FilterKey[]).filter((key) => filters[key]).map((key) => `${key}: ${filters[key]}`).join(", ");
  return <>
    <Panel title={`Workboard · ${payload.mission.title}`} hint={`${payload.total} tasks · composed ${formatTimestamp(payload.composed_at)}`}>
      <div className="workboard-toolbar"><span className={`badge ${stream.state === "live" ? "badge-strong" : "badge-weak"}`}>{stream.state === "live" ? "connection live" : `connection ${stream.state}`}</span><button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>{query.isFetching ? "Refreshing…" : "Refresh"}</button></div>
      <div className="workboard-filters">
        <label>state<select aria-label="filter workboard by state" value={filters.state} onChange={(event) => setFilter("state", event.target.value)}><option value="">all states</option>{payload.filters.states.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>agent<select aria-label="filter workboard by agent" value={filters.agent} onChange={(event) => setFilter("agent", event.target.value)}><option value="">all agents</option>{payload.filters.agents.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>runtime<select aria-label="filter workboard by runtime" value={filters.runtime} onChange={(event) => setFilter("runtime", event.target.value)}><option value="">all runtimes</option>{payload.filters.runtimes.map((value) => <option key={value}>{value}</option>)}</select></label>
        {([ ["workspace", "has workspace"], ["lease", "has active lease"], ["approval", "needs approval"], ["blocked", "blocked"] ] as Array<[FilterKey, string]>).map(([key, label]) => <label className="workboard-check" key={key}><input type="checkbox" aria-label={`filter workboard: ${label}`} checked={filters[key] === "yes"} onChange={(event) => setFilter(key, event.target.checked ? "yes" : "")} />{label}</label>)}
      </div>
    </Panel>
    {notice ? <p className="tight error" role="status">{notice}</p> : null}
    {payload.degraded.length ? <aside className="workboard-degraded" role="status"><strong>Board data degraded</strong><ul>{payload.degraded.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></aside> : null}
    {payload.total === 0 ? <Panel title="No tasks"><EmptyState title="This mission has no tasks">The daemon returned no cards for this mission.</EmptyState></Panel> : null}
    {payload.total > 0 && payload.columns.every((column) => column.count === 0) ? <Panel title="No cards in columns"><EmptyState title="The mission has zero cards in every column">The board response contains tasks but none are assigned to its returned columns.</EmptyState></Panel> : null}
    {visibleByLane.every((column) => column.tasks.length === 0) && payload.total > 0 && filterLabel ? <p className="workboard-filter-empty">No cards shown; filters are hiding everything ({filterLabel}).</p> : null}
    <div className="workboard-columns">{visibleByLane.map((column) => <section className="workboard-column" key={column.lane} aria-label={`${column.lane} column`}><header><h2>{column.lane}</h2><span className="badge badge-unknown">{column.count}</span></header>{column.tasks.length === 0 ? <p className="tight faint">{column.count === 0 ? "No cards in this lane." : `All ${column.count} cards hidden by filters.`}</p> : column.tasks.map((task) => <BoardCard key={task.task_id} task={task} missionId={missionId} onAction={(id, action) => void doAction(id, action)} />)}</section>)}</div>
    <Panel title="Waves" hint="server-composed dependency layers"><div className="workboard-waves">{payload.waves.length === 0 ? <p className="tight faint">No waves were returned.</p> : payload.waves.map((wave) => <div key={wave.wave}><strong>wave {wave.wave}</strong><span className="mono">{wave.tasks.join(" · ") || "no tasks"}</span></div>)}</div></Panel>
    <p className="faint"><Link to="/missions/$missionId" params={{ missionId }}>Back to mission</Link></p>
  </>;
}
