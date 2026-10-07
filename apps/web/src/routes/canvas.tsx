import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Background, Controls, MiniMap, ReactFlow, Handle, Position,
  type Connection, type Edge, type Node, type NodeProps, useEdgesState, useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ApiRefusal, CanvasEdge, CanvasNode, MissionCanvas, addTaskDependency,
  assignTask, createTask, fetchMissionCanvas, removeTaskDependency,
} from "../api";
import { EmptyState, Loading, Panel } from "../components/Panel";
import { useStream } from "../stream";

interface CanvasNodeData extends Record<string, unknown> { entity: CanvasNode }
type FlowNode = Node<CanvasNodeData>;
type FlowEdge = Edge<{ canonical: CanvasEdge }>;

const EVENT_NAMESPACES = /^(task|agent|run|session|workspace|approval|artifact|usage)\./;
const TYPE_NAMES = ["task", "agent", "run", "session", "workspace", "approval", "artifact"] as const;

function text(value: unknown): string | null { return typeof value === "string" && value.length > 0 ? value : null; }
function number(value: unknown): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function errorMessage(error: unknown): string {
  if (error instanceof ApiRefusal) return error.detail;
  return error instanceof Error ? error.message : String(error);
}
function stateClass(state: string | null): string {
  if (state === "done" || state === "ready") return "badge badge-strong";
  if (state === "failed" || state === "blocked" || state === "cancelled") return "badge badge-weak";
  if (state === "running" || state === "review" || state === "pending") return "badge badge-moderate";
  return "badge badge-unknown";
}

function NodeFrame({ entity, className, children }: { entity: CanvasNode; className: string; children: React.ReactNode }) {
  return <article className={`canvas-node ${className}`} aria-label={`${entity.entity_type}: ${entity.label}`}>
    <Handle type="target" position={Position.Left} />
    <span className="canvas-node-kind">{entity.entity_type}</span>
    {children}
    <Handle type="source" position={Position.Right} />
  </article>;
}

function TaskNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity; const m = entity.metadata; const writer = record(m.writer);
  const approvals = number(m.approvals_pending); const artifacts = number(m.artifacts);
  return <NodeFrame entity={entity} className="canvas-node-task">
    <strong className="canvas-task-title">{entity.label}</strong>
    <span className={stateClass(entity.state)}>{entity.state ?? "state unknown"}</span>
    {text(m.agent_id) ? <small>agent · <code>{text(m.agent_id)}</code></small> : null}
    {text(m.run_id) ? <small>run · <code>{text(m.run_id)}</code></small> : null}
    {m.has_workspace === true ? <small className="canvas-marker">workspace</small> : null}
    {writer.active === true ? <small className="canvas-marker">writer generation {number(writer.generation) ?? "unknown"}</small> : null}
    {approvals !== null && approvals > 0 ? <small className="canvas-marker canvas-warning">approval required · {approvals}</small> : null}
    {artifacts !== null && artifacts > 0 ? <small className="canvas-marker">artifacts · {artifacts}</small> : null}
  </NodeFrame>;
}
function AgentNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity; const m = entity.metadata; const assigned = Array.isArray(m.assigned_tasks) ? m.assigned_tasks.length : null;
  return <NodeFrame entity={entity} className="canvas-node-agent">
    <strong>{entity.label}</strong><code>{entity.entity_id}</code>
    {text(m.role) ? <small>{text(m.role)}</small> : null}
    <small>assigned tasks · {assigned === null ? "unknown" : assigned}</small>
    {text(m.current_run_id) ? <small>current run · <code>{text(m.current_run_id)}</code></small> : null}
  </NodeFrame>;
}
function RunNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity;
  return <NodeFrame entity={entity} className="canvas-node-run"><code>{entity.entity_id}</code><span className={stateClass(entity.state)}>{entity.state ?? "state unknown"}</span></NodeFrame>;
}
function SessionNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity;
  return <NodeFrame entity={entity} className="canvas-node-session"><strong>{entity.label}</strong></NodeFrame>;
}
function WorkspaceNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity; const m = entity.metadata; const enforcement = record(m.enforcement);
  const dirty = m.measured !== true ? "dirty not measured" : m.dirty === true ? "dirty" : m.dirty === false ? "clean" : "dirty not measured";
  const writerSummary = entity.summary.split(" · ").find((part) => part.startsWith("writer ") || part === "no writer");
  return <NodeFrame entity={entity} className="canvas-node-workspace">
    <strong className="canvas-wrap">{text(m.locator) ?? entity.label}</strong>
    <small>{text(m.provider) ?? "provider unknown"} · {text(m.state) ?? "state unknown"}</small>
    <small>{dirty}</small>
    {writerSummary ? <small className={writerSummary.includes("expired") ? "canvas-warning" : ""}>{writerSummary}</small> : null}
    <small>write isolation: {text(enforcement.write_isolation) ?? "unknown"}</small>
    <small>filesystem isolation: {text(enforcement.filesystem_isolation) ?? "unknown"}</small>
  </NodeFrame>;
}
function ApprovalNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity; const m = entity.metadata; const attention = m.requires_human_attention === true;
  return <NodeFrame entity={entity} className={`canvas-node-approval${attention ? " canvas-node-critical" : ""}`}>
    <strong>{entity.label}</strong><span className={stateClass(entity.state)}>{entity.state ?? "state unknown"}</span>
    <small>risk · {text(m.risk_level) ?? "unknown"}</small><small>action · {text(m.action_type) ?? "unknown"}</small>
    {attention ? <b className="canvas-warning">human attention required</b> : null}
  </NodeFrame>;
}
function ArtifactNode({ data }: NodeProps<FlowNode>) {
  const entity = data.entity; const m = entity.metadata;
  const size = m.size === null || m.size === undefined ? "size unknown" : `${String(m.size)} bytes`;
  return <NodeFrame entity={entity} className="canvas-node-artifact">
    <strong className="canvas-wrap">{entity.label}</strong><small>{text(m.mime) ?? "mime unknown"} · {size}</small>
    <small>integrity · {text(m.integrity) ?? "unknown"}</small>
  </NodeFrame>;
}
const nodeTypes = { task: TaskNode, agent: AgentNode, run: RunNode, session: SessionNode, workspace: WorkspaceNode, approval: ApprovalNode, artifact: ArtifactNode };

function initialPositions(payload: MissionCanvas): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const entities = new Map(payload.nodes.map((node) => [node.key, node]));
  const taskKeys = new Set(payload.nodes.filter((node) => node.entity_type === "task").map((node) => node.key));
  const waveIndex = new Map<string, number>();
  for (const wave of payload.waves) for (const id of wave.tasks) waveIndex.set(`task:${id}`, wave.wave);
  const agentNodes = payload.nodes.filter((node) => node.entity_type === "agent").sort((a, b) => a.entity_id.localeCompare(b.entity_id));
  const taskNodes = payload.nodes.filter((node) => node.entity_type === "task");
  agentNodes.forEach((node, index) => positions.set(node.key, { x: 90 + index * 320, y: 30 }));
  const byWave = new Map<number, CanvasNode[]>();
  taskNodes.forEach((node) => { const wave = waveIndex.get(node.key); if (wave === undefined) return; const list = byWave.get(wave) ?? []; list.push(node); byWave.set(wave, list); });
  for (const [wave, tasks] of [...byWave].sort(([a], [b]) => a - b)) {
    tasks.sort((a, b) => a.entity_id.localeCompare(b.entity_id));
    tasks.forEach((task, row) => {
      const x = 90 + wave * 1100; const y = 190 + row * 640;
      positions.set(task.key, { x, y });
      const related = payload.edges.filter((edge) => edge.source === task.key || edge.target === task.key)
        .map((edge) => entities.get(edge.source === task.key ? edge.target : edge.source))
        .filter((candidate): candidate is CanvasNode => Boolean(candidate && !taskKeys.has(candidate.key) && candidate.entity_type !== "agent"));
      const unique = [...new Map(related.map((item) => [item.key, item])).values()];
      // The operational nodes sit in a two-column block to the right of their task. The spacing is
      // generous on purpose: an artifact node shows a digest and a workspace node shows a locator, so
      // a tight grid puts one node on top of another and makes a node unclickable.
      unique.forEach((item, index) =>
        positions.set(item.key, { x: x + 370 + (index % 2) * 360, y: y - 40 + Math.floor(index / 2) * 200 }),
      );
    });
  }
  // Any canonical nodes not situated by a wave remain visible in a deterministic rail.
  payload.nodes.forEach((node, index) => { if (!positions.has(node.key)) positions.set(node.key, { x: 90 + (index % 4) * 360, y: 1400 + Math.floor(index / 4) * 200 }); });
  return positions;
}
function savedPositions(missionId: string): Record<string, { x: number; y: number }> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(`mission-canvas:${missionId}`) ?? "{}");
    if (!parsed || typeof parsed !== "object") return {};
    const result: Record<string, { x: number; y: number }> = {};
    for (const [key, value] of Object.entries(parsed)) {
      const item = record(value); const x = number(item.x); const y = number(item.y);
      if (x !== null && y !== null) result[key] = { x, y };
    }
    return result;
  } catch { return {}; }
}
function hrefFor(entity: CanvasNode, missionId: string): string | null {
  const id = encodeURIComponent(entity.entity_id);
  switch (entity.entity_type) {
    case "task": return `/missions/${encodeURIComponent(missionId)}?task=${id}`;
    case "artifact": return `/artifacts/${id}`;
    case "approval": return "/approvals";
    case "agent": return `/agents/${id}`;
    case "run": return "/events";
    case "workspace": return `/missions/${encodeURIComponent(missionId)}?workspace=${id}`;
    case "session": return null;
  }
}

export function CanvasPage({ missionId }: { missionId: string }) {
  const stream = useStream();
  const query = useQuery({ queryKey: ["mission-canvas", missionId], queryFn: () => fetchMissionCanvas(missionId), retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([]);
  const [selectedNode, setSelectedNode] = useState<CanvasNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<CanvasEdge | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showTaskForm, setShowTaskForm] = useState(false);
  const [title, setTitle] = useState("");
  const [dependencies, setDependencies] = useState<string[]>([]);
  const lastSeq = useRef(0); const timer = useRef<number | null>(null);
  const payload = query.data;

  useEffect(() => {
    const incoming = stream.events.filter((event) => event.seq > lastSeq.current);
    lastSeq.current = Math.max(lastSeq.current, stream.lastSeq);
    if (!incoming.some((event) => EVENT_NAMESPACES.test(event.kind))) return;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { void query.refetch(); }, 250);
  }, [stream.events, stream.lastSeq, query.refetch]);
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current); }, []);

  useEffect(() => {
    if (!payload) return;
    const initial = initialPositions(payload); const saved = savedPositions(missionId);
    setNodes(payload.nodes.map((entity) => ({
      id: entity.key, type: entity.entity_type, data: { entity },
      position: saved[entity.key] ?? initial.get(entity.key) ?? { x: 0, y: 0 },
    })));
    setEdges(payload.edges.map((edge) => ({
      id: edge.key, source: edge.source, target: edge.target, label: edge.label,
      data: { canonical: edge }, animated: false,
      style: { stroke: edge.kind === "depends_on" ? "#d8a25a" : "#74675c" },
      labelStyle: { fill: "#ece5df", fontSize: 11 },
    })));
    setSelectedNode((current) => current ? payload.nodes.find((item) => item.key === current.key) ?? null : null);
    setSelectedEdge((current) => current ? payload.edges.find((item) => item.key === current.key) ?? null : null);
  }, [payload, missionId, setNodes, setEdges]);

  const byId = useMemo(() => new Map((payload?.nodes ?? []).map((entity) => [entity.key, entity])), [payload]);
  const refreshAfter = async () => { await query.refetch(); };
  const onMutationFailure = async (error: unknown) => { setNotice(errorMessage(error)); await refreshAfter(); };

  const onConnect = useCallback((connection: Connection) => {
    if (!payload || !connection.source || !connection.target) return;
    const source = byId.get(connection.source); const target = byId.get(connection.target);
    if (!source || !target) return;
    if (source.entity_type === "task" && target.entity_type === "task") {
      const sourceTask = source.entity_id; const targetTask = target.entity_id;
      if (!window.confirm(`Make ${target.label} depend on ${source.label}?\n\n${target.label} will depend on ${source.label}.`)) return;
      void addTaskDependency(targetTask, sourceTask).then(async () => { setNotice(`Dependency accepted: ${target.label} depends on ${source.label}.`); await refreshAfter(); }).catch(onMutationFailure);
    } else if (source.entity_type === "agent" && target.entity_type === "task") {
      if (!window.confirm(`Assign ${source.label} to ${target.label}?`)) return;
      void assignTask(target.entity_id, source.entity_id).then(async () => { setNotice(`Assignment accepted: ${source.label} to ${target.label}.`); await refreshAfter(); }).catch(onMutationFailure);
    }
  }, [payload, byId]);

  const deleteSelectedDependency = async () => {
    if (!selectedEdge || selectedEdge.kind !== "depends_on" || !selectedEdge.mutable) return;
    const dependent = byId.get(selectedEdge.target); const dependency = byId.get(selectedEdge.source);
    if (!dependent || !dependency || dependent.entity_type !== "task" || dependency.entity_type !== "task") return;
    if (!window.confirm(`Remove ${dependent.label}'s dependency on ${dependency.label}?`)) return;
    try { await removeTaskDependency(dependent.entity_id, dependency.entity_id); setNotice("Dependency removal accepted."); await refreshAfter(); }
    catch (error) { await onMutationFailure(error); }
  };
  const create = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!title.trim()) return;
    try {
      await createTask(missionId, { title: title.trim(), description: "", dependencies, requiresArtifact: false, ownerAgent: null });
      setTitle(""); setDependencies([]); setShowTaskForm(false); setNotice("Task accepted by the daemon."); await refreshAfter();
    } catch (error) { await onMutationFailure(error); }
  };
  const persistPositions = useCallback((next: FlowNode[]) => {
    try {
      const current = savedPositions(missionId);
      for (const node of next) current[node.id] = node.position;
      const valid = new Set((payload?.nodes ?? []).map((node) => node.key));
      for (const key of Object.keys(current)) if (!valid.has(key)) delete current[key];
      localStorage.setItem(`mission-canvas:${missionId}`, JSON.stringify(current));
    } catch { /* layout preference storage is optional */ }
  }, [missionId, payload]);
  const changeNodes = useCallback((changes: Parameters<typeof onNodesChange>[0]) => {
    onNodesChange(changes);
    if (changes.some((change) => change.type === "position" && change.position)) {
      setNodes((current) => { const updated = current.map((node) => {
        const change = changes.find((item) => item.type === "position" && item.id === node.id);
        return change?.type === "position" && change.position ? { ...node, position: change.position } : node;
      }); persistPositions(updated); return updated; });
    }
  }, [onNodesChange, persistPositions, setNodes]);

  if (query.isLoading) return <Loading label="reading canonical mission canvas" />;
  if (query.isError) return <Panel title="Canvas unavailable" variant="error"><p className="error" role="alert">{errorMessage(query.error)}</p><button type="button" onClick={() => void query.refetch()}>Retry canvas</button></Panel>;
  if (!payload) return <Panel title="Canvas unavailable" variant="error"><p role="alert">The daemon returned no canvas payload.</p><button type="button" onClick={() => void query.refetch()}>Retry canvas</button></Panel>;
  const node = selectedNode;
  const selectedUrl = node ? hrefFor(node, missionId) : null;
  return <>
    <Panel title={`Canvas · ${payload.mission.title}`} hint={`${payload.counts.nodes} nodes · ${payload.counts.edges} edges`}>
      <div className="canvas-toolbar">
        <span className={`badge ${stream.state === "live" ? "badge-strong" : "badge-weak"}`} aria-label={`event connection ${stream.state}`}>connection {stream.state}</span>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>{query.isFetching ? "Refreshing…" : "Refresh"}</button>
        <button type="button" onClick={() => setShowTaskForm((visible) => !visible)} aria-expanded={showTaskForm}>+ Task</button>
        <span className="canvas-boundary">Control transfer is not available yet because actor identity is not authenticated.</span>
      </div>
      {showTaskForm ? <form className="canvas-create" onSubmit={(event) => void create(event)}>
        <label>Task title<input aria-label="new task title" required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <fieldset><legend>Optional dependencies</legend>{payload.nodes.filter((item) => item.entity_type === "task").map((item) => <label key={item.key}><input type="checkbox" checked={dependencies.includes(item.entity_id)} onChange={(event) => setDependencies((current) => event.target.checked ? [...current, item.entity_id] : current.filter((id) => id !== item.entity_id))} />{item.label}</label>)}</fieldset>
        <button type="submit" disabled={!title.trim()}>Create task</button><button type="button" onClick={() => setShowTaskForm(false)}>Close</button>
      </form> : null}
    </Panel>
    {notice ? <p className="canvas-notice" role="status">{notice}</p> : null}
    {payload.degraded.length > 0 ? <aside className="workboard-degraded" role="status"><strong>Canvas data degraded</strong><ul>{payload.degraded.map((message, index) => <li key={`${index}-${message}`}>{message}</li>)}</ul></aside> : null}
    {payload.nodes.length === 0 ? <Panel title="Empty mission"><EmptyState title="This mission has no canvas entities">The daemon returned no nodes or relationships for this mission.</EmptyState></Panel> : <div className="canvas-layout">
      <section className="canvas-flow" aria-label="Mission operational canvas">
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={changeNodes} onEdgesChange={onEdgesChange} onConnect={onConnect}
          deleteKeyCode={null}
          isValidConnection={(connection) => {
            const source = byId.get(connection.source); const target = byId.get(connection.target);
            return Boolean(source && target && ((source.entity_type === "task" && target.entity_type === "task") || (source.entity_type === "agent" && target.entity_type === "task")));
          }}
          onNodeClick={(_event, selected) => { setSelectedNode(byId.get(selected.id) ?? null); setSelectedEdge(null); }}
          onEdgeClick={(_event, selected) => { setSelectedEdge(selected.data?.canonical ?? null); setSelectedNode(null); }}
          onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null); }} fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.15} maxZoom={1.6}>
          <Background color="#423931" gap={24} /><MiniMap pannable zoomable nodeColor={(flowNode) => `var(--canvas-${String(flowNode.type)})`} /><Controls />
        </ReactFlow>
      </section>
      <aside className="canvas-inspector" aria-label="Canvas inspector">
        {node ? <>
          <span className="canvas-node-kind">{node.entity_type}</span><h2>{node.label}</h2>
          <p className="tight faint">{node.entity_id}</p><p>{node.summary}</p>
          <h3>Canonical metadata</h3><pre>{JSON.stringify(node.metadata, null, 2)}</pre>
          {selectedUrl ? <a className="canvas-open-link" href={selectedUrl}>Open {node.entity_type} surface</a> : null}
        </> : selectedEdge ? <>
          <span className="canvas-node-kind">relationship</span><h2>{selectedEdge.label || selectedEdge.kind}</h2>
          <p className="tight">{byId.get(selectedEdge.source)?.label ?? selectedEdge.source} → {byId.get(selectedEdge.target)?.label ?? selectedEdge.target}</p>
          {selectedEdge.mutable && selectedEdge.kind === "depends_on" ? <button type="button" onClick={() => void deleteSelectedDependency()}>Remove dependency</button> : null}
        </> : <p className="faint">Select a node or relationship to inspect its canonical details.</p>}
      </aside>
    </div>}
  </>;
}

export { TYPE_NAMES };
