/**
 * Meta-Harness desktop plugin.
 *
 * Hot-loaded by Hermes Desktop from
 *   ~/.hermes/desktop-plugins/meta-harness/plugin.js
 *
 * Registers:
 *   - /meta-harness route with sidebar nav
 *   - command-palette entries
 *   - status-bar chips (active topology, run count, engine availability)
 *
 * Talks to its own backend at /api/plugins/meta-harness/* via ctx.rest().
 * Live updates use ctx.socket() with a polling fallback for OAuth remotes.
 *
 * Theme: uses Hermes theme variables. No hardcoded colors.
 */

import {
  Button,
  cn,
  Codicon,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  host,
  PALETTE_AREA,
  QueryClient,
  queryClient,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Skeleton,
  StatusDot,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  Textarea,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tip,
  useMutation,
  useQuery,
  useValue,
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'

const ID = 'meta-harness'

// ---------------------------------------------------------------------------
// REST helpers — all backend paths share the plugin-scoped prefix.
// ---------------------------------------------------------------------------

async function rest(path, opts = {}) {
  return ctx_rest(path, opts)
}

function ctx_rest(path, opts) {
  // `ctx.rest` is the SDK helper. We fall back to a fetch() with the
  // session token if ctx.rest is unavailable.
  if (typeof ctx !== 'undefined' && ctx && typeof ctx.rest === 'function') {
    return ctx.rest(path, opts)
  }
  // Manual fallback — should not happen in a real Hermes shell.
  return fetch('/api/plugins/' + ID + path, {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  }).then(r => r.json())
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

function useStatus() {
  return useQuery({
    queryKey: [ID, 'status'],
    queryFn: () => rest('/status'),
    refetchInterval: 5000,
    staleTime: 1500,
  })
}

function useRuns() {
  return useQuery({
    queryKey: [ID, 'runs'],
    queryFn: () => rest('/runs?limit=30'),
    refetchInterval: 2000,
    staleTime: 1000,
  })
}

function useRun(runId) {
  return useQuery({
    queryKey: [ID, 'run', runId],
    queryFn: () => rest('/runs/' + runId),
    enabled: !!runId,
    refetchInterval: 1500,
    staleTime: 500,
  })
}

function useRunEvents(runId, sinceRef) {
  return useQuery({
    queryKey: [ID, 'events', runId, sinceRef.current],
    queryFn: () => rest('/runs/' + runId + '/events?since_id=' + (sinceRef.current || 0)),
    enabled: !!runId,
    refetchInterval: 1500,
    staleTime: 300,
  })
}

function useRunGraph(runId) {
  return useQuery({
    queryKey: [ID, 'graph', runId],
    queryFn: () => rest('/runs/' + runId + '/graph'),
    enabled: !!runId,
    refetchInterval: 2000,
    staleTime: 500,
  })
}

function useTopologies() {
  return useQuery({
    queryKey: [ID, 'topologies'],
    queryFn: () => rest('/topologies'),
    refetchInterval: 30000,
  })
}

function useCharacterPacks() {
  return useQuery({
    queryKey: [ID, 'characters'],
    queryFn: () => rest('/character-packs'),
    refetchInterval: 15000,
  })
}

function usePlugins() {
  return useQuery({
    queryKey: [ID, 'plugins'],
    queryFn: () => rest('/plugins'),
    refetchInterval: 8000,
  })
}

// ---------------------------------------------------------------------------
// Status bar chip
// ---------------------------------------------------------------------------

function StatusChip() {
  const status = useStatus()
  const data = status.data
  if (status.isLoading) return jsx(Skeleton, { className: 'h-4 w-24' })
  if (status.error) {
    return jsx(Tip, {
      label: 'Meta-Harness backend unreachable',
      children: jsx(StatusDot, { variant: 'error' }),
    })
  }
  const h = data?.engines?.hermes?.available
  const p = data?.engines?.pi?.available
  const tops = data?.topologies?.length || 0
  return jsx(Tip, {
    label: `Hermes:${h ? 'on' : 'off'}  Pi:${p ? 'on' : 'off'}  Topologies:${tops}`,
    children: jsxs('span', {
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem]',
        'text-(--ui-text-tertiary)'
      ),
      children: [
        jsx('span', { 'aria-hidden': true, children: '◆' }),
        jsx('span', { children: 'Meta-Harness' }),
      ],
    }),
  })
}

// ---------------------------------------------------------------------------
// Command center
// ---------------------------------------------------------------------------

function CommandCenter() {
  const [tab, setTab] = useState('run')
  const [activeRunId, setActiveRunId] = useState(null)
  const status = useStatus()
  const runs = useRuns()
  const topologies = useTopologies()
  const packs = useCharacterPacks()

  const engineHermes = status.data?.engines?.hermes?.available
  const enginePi = status.data?.engines?.pi?.available
  const fallbackModel = status.data?.engines?.hermes?.default_model

  return jsxs(Fragment, {
    children: [
      jsx(HeaderBar, { status: status.data, runsCount: runs.data?.runs?.length || 0 }),
      jsx('div', {
        className: 'flex h-full min-h-0',
        children: jsxs('div', {
          className: 'flex flex-1 flex-col min-w-0',
          children: [
            jsxs(Tabs, {
              value: tab,
              onValueChange: setTab,
              children: [
                jsxs(TabsList, {
                  children: [
                    jsx(TabsTrigger, { value: 'run', children: 'Run' }),
                    jsx(TabsTrigger, { value: 'agents', children: 'Agents' }),
                    jsx(TabsTrigger, { value: 'graph', children: 'Graph' }),
                    jsx(TabsTrigger, { value: 'timeline', children: 'Timeline' }),
                    jsx(TabsTrigger, { value: 'office', children: 'Office' }),
                    jsx(TabsTrigger, { value: 'plugins', children: 'Plugins' }),
                    jsx(TabsTrigger, { value: 'characters', children: 'Characters' }),
                  ],
                }),
                jsxs('div', {
                  className: 'flex-1 min-h-0 overflow-auto',
                  children: [
                    jsx(TabsContent, {
                      value: 'run',
                      children: jsx(RunPanel, {
                        topologies: topologies.data?.topologies || [],
                        engines: { hermes: engineHermes, pi: enginePi },
                        model: fallbackModel,
                        onRunCreated: (id) => { setActiveRunId(id); setTab('agents') },
                      }),
                    }),
                    jsx(TabsContent, {
                      value: 'agents',
                      children: jsx(AgentsPanel, {
                        runId: activeRunId,
                        runs: runs.data?.runs || [],
                        onSelect: setActiveRunId,
                      }),
                    }),
                    jsx(TabsContent, {
                      value: 'graph',
                      children: jsx(GraphPanel, { runId: activeRunId }),
                    }),
                    jsx(TabsContent, {
                      value: 'timeline',
                      children: jsx(TimelinePanel, { runId: activeRunId }),
                    }),
                    jsx(TabsContent, {
                      value: 'office',
                      children: jsx(OfficePanel, {
                        runId: activeRunId,
                        packId: packs.data?.active,
                      }),
                    }),
                    jsx(TabsContent, {
                      value: 'plugins',
                      children: jsx(PluginsPanel, {}),
                    }),
                    jsx(TabsContent, {
                      value: 'characters',
                      children: jsx(CharactersPanel, {}),
                    }),
                  ],
                }),
              ],
            }),
          ],
        }),
      }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Header bar — title + live counters
// ---------------------------------------------------------------------------

function HeaderBar({ status, runsCount }) {
  const activeTopology = status?.topologies?.[0] || 'solo'
  const engHermes = status?.engines?.hermes?.available ? 'hermes' : 'off'
  const engPi = status?.engines?.pi?.available ? 'pi' : 'off'
  return jsxs('div', {
    className: cn(
      'flex items-center gap-3 border-b border-(--ui-stroke-secondary)',
      'px-4 py-2 text-(--ui-text-secondary) bg-(--ui-bg-elevated)'
    ),
    children: [
      jsx('div', {
        className: 'flex items-center gap-2',
        children: jsx('strong', {
          className: 'text-(--ui-text-primary) font-semibold tracking-tight',
          children: 'Meta-Harness',
        }),
      }),
      jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
      jsx(Tip, {
        label: 'Active topology preset',
        children: jsxs('span', { className: 'text-xs', children: ['topology:', jsx('span', { className: 'ml-1 text-(--ui-text-primary)', children: activeTopology })] }),
      }),
      jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
      jsx(Tip, {
        label: 'Engine availability',
        children: jsxs('span', { className: 'text-xs', children: ['engines:', jsx('span', { className: 'ml-1', children: `${engHermes}/${engPi}` })] }),
      }),
      jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
      jsx(Tip, {
        label: 'Recent runs',
        children: jsxs('span', { className: 'text-xs', children: ['runs:', jsx('span', { className: 'ml-1 text-(--ui-text-primary)', children: runsCount })] }),
      }),
      jsx('span', { className: 'ml-auto text-(--ui-text-quaternary)', children: 'v0.1.0' }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Run panel
// ---------------------------------------------------------------------------

function RunPanel({ topologies, engines, model, onRunCreated }) {
  const [task, setTask] = useState('')
  const [topologyId, setTopologyId] = useState('solo')
  const [engine, setEngine] = useState('hermes')
  const [role, setRole] = useState('builder')

  const create = useMutation({
    mutationFn: () => rest('/runs', {
      method: 'POST',
      body: { task, topology: topologyId, engine, role },
    }),
    onSuccess: (data) => { if (data?.run_id) onRunCreated(data.run_id) },
  })

  const engineChoices = []
  if (engines.hermes) engineChoices.push({ id: 'hermes', label: 'hermes' })
  if (engines.pi) engineChoices.push({ id: 'pi', label: 'pi' })
  if (engineChoices.length === 0) {
    engineChoices.push({ id: 'hermes', label: 'hermes (offline)' })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col gap-4 p-4',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          jsx(Field, {
            label: 'Topology',
            children: jsx(Picker, {
              value: topologyId,
              onChange: setTopologyId,
              options: topologies.length ? topologies.map(t => ({ id: t.id, label: t.id })) : [{ id: 'solo', label: 'solo' }],
            }),
          }),
          jsx(Field, {
            label: 'Engine',
            children: jsx(Picker, {
              value: engine,
              onChange: setEngine,
              options: engineChoices,
            }),
          }),
          jsx(Field, {
            label: 'Role',
            children: jsx(Picker, {
              value: role,
              onChange: setRole,
              options: [
                { id: 'builder', label: 'builder' },
                { id: 'architect', label: 'architect' },
                { id: 'reviewer', label: 'reviewer' },
                { id: 'validator', label: 'validator' },
                { id: 'visual-reviewer', label: 'visual-reviewer' },
              ],
            }),
          }),
          jsxs('div', {
            className: 'flex items-center gap-1 text-xs text-(--ui-text-tertiary)',
            children: ['Model: ', jsx('span', { className: 'text-(--ui-text-primary)', children: model || 'inherit' })],
          }),
        ],
      }),
      jsxs('div', {
        className: 'flex flex-col gap-2 min-h-0 flex-1',
        children: [
          jsx('label', {
            className: 'text-xs text-(--ui-text-tertiary)',
            children: 'Task',
          }),
          jsx(Textarea, {
            value: task,
            onChange: (e) => setTask(e.target.value),
            placeholder: 'Describe the task for the topology…',
            className: 'flex-1 min-h-[160px] font-mono text-sm',
          }),
        ],
      }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Button, {
            disabled: !task.trim() || create.isPending,
            onClick: () => create.mutate(),
            children: create.isPending ? jsxs(Fragment, { children: [jsx(GlyphSpinner, {}), 'starting…'] }) : 'Run',
          }),
          create.error && jsx('span', { className: 'text-xs text-(--ui-text-error)', children: 'Failed to start run.' }),
          create.data?.error && jsx('span', { className: 'text-xs text-(--ui-text-error)', children: create.data.error }),
        ],
      }),
    ],
  })
}

function Field({ label, children }) {
  return jsxs('label', {
    className: 'flex flex-col gap-1 text-xs text-(--ui-text-tertiary)',
    children: [jsx('span', { children: label }), children],
  })
}

function Picker({ value, onChange, options }) {
  return jsx(Select, {
    value,
    onValueChange: onChange,
    children: jsxs(Fragment, {
      children: [
        jsx(SelectTrigger, {
          className: 'w-44',
          children: jsx(SelectValue, { placeholder: 'pick…' }),
        }),
        jsx(SelectContent, {
          children: options.map(o =>
            jsx(SelectItem, { value: o.id, children: o.label }, o.id)
          ),
        }),
      ],
    }),
  })
}

// ---------------------------------------------------------------------------
// Agents panel
// ---------------------------------------------------------------------------

function AgentsPanel({ runId, runs, onSelect }) {
  const run = useRun(runId)

  if (!runId) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-3 p-4',
      children: [
        jsx('h3', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Recent runs' }),
        jsx(RunList, { runs, onSelect }),
      ],
    })
  }
  const data = run.data
  if (run.isLoading) return jsx(Skeleton, { className: 'h-32 w-full m-4' })
  if (run.error) return jsx(ErrorState, { title: 'Failed to load run' })
  const agents = data?.agents || []
  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-2 text-(--ui-text-secondary)',
        children: [
          jsx(Button, {
            variant: 'ghost',
            onClick: () => onSelect(null),
            children: '← runs',
          }),
          jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
          jsx('strong', { className: 'text-(--ui-text-primary)', children: data?.run?.id }),
          jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
          jsx('span', { className: 'text-xs', children: data?.run?.topology }),
          jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
          jsx('span', {
            className: cn('text-xs px-1.5 py-0.5 rounded',
              data?.run?.status === 'completed' ? 'bg-(--ui-bg-success) text-(--ui-text-success)'
              : data?.run?.status === 'failed' ? 'bg-(--ui-bg-error) text-(--ui-text-error)'
              : 'bg-(--ui-bg-elevated) text-(--ui-text-tertiary)'),
            children: data?.run?.status || 'pending',
          }),
          jsx('div', { className: 'ml-auto' }),
          jsx(Button, {
            variant: 'ghost',
            disabled: data?.run?.status === 'cancelled' || data?.run?.status === 'completed',
            onClick: () => rest('/runs/' + data.run.id + '/cancel', { method: 'POST' }),
            children: 'cancel',
          }),
        ],
      }),
      jsx('div', {
        className: 'flex-1 min-h-0 overflow-auto p-4',
        children: agents.length === 0
          ? jsx(EmptyState, { title: 'No agents yet', description: 'The topology has not spawned agents.' })
          : jsx('div', {
              className: 'grid gap-3',
              children: agents.map(a => jsx(AgentCard, { agent: a }, a.id)),
            }),
      }),
    ],
  })
}

function AgentCard({ agent }) {
  const state = agent.state || 'idle'
  return jsxs('div', {
    className: cn(
      'flex items-center gap-3 rounded border border-(--ui-stroke-secondary)',
      'bg-(--ui-bg-elevated) px-3 py-2'
    ),
    children: [
      jsx('span', {
        className: cn(
          'inline-block size-2 rounded-full',
          state === 'completed' ? 'bg-(--ui-text-success)'
          : state === 'failed' ? 'bg-(--ui-text-error)'
          : state === 'cancelled' ? 'bg-(--ui-text-quaternary)'
          : 'bg-(--ui-accent) animate-pulse'
        ),
      }),
      jsxs('div', {
        className: 'flex-1 min-w-0',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx('span', { className: 'font-mono text-sm text-(--ui-text-primary)', children: agent.id }),
              jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: agent.role }),
            ],
          }),
          jsxs('div', {
            className: 'flex items-center gap-2 text-xs text-(--ui-text-tertiary)',
            children: [
              jsx('span', { children: agent.engine }),
              jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
              jsx('span', { children: agent.model || 'inherit' }),
              jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
              jsx('span', { children: state }),
            ],
          }),
        ],
      }),
    ],
  })
}

function RunList({ runs, onSelect }) {
  if (!runs || runs.length === 0) {
    return jsx(EmptyState, { title: 'No runs yet', description: 'Start one from the Run tab.' })
  }
  return jsx('div', {
    className: 'grid gap-2',
    children: runs.map(r => jsxs('button', {
      onClick: () => onSelect(r.id),
      className: cn(
        'flex items-center gap-3 rounded border border-(--ui-stroke-secondary)',
        'bg-(--ui-bg-elevated) px-3 py-2 text-left hover:bg-(--ui-bg-hovered)'
      ),
      children: [
        jsx(StatusDot, {
          variant: r.status === 'completed' ? 'success'
            : r.status === 'failed' ? 'error'
            : r.status === 'cancelled' ? 'neutral'
            : 'active',
        }),
        jsxs('div', {
          className: 'flex-1 min-w-0',
          children: [
            jsx('div', { className: 'font-mono text-xs text-(--ui-text-secondary)', children: r.id }),
            jsx('div', { className: 'truncate text-sm text-(--ui-text-primary)', children: r.task }),
          ],
        }),
        jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: r.topology }),
      ],
    }, r.id)),
  })
}

// ---------------------------------------------------------------------------
// Graph panel
// ---------------------------------------------------------------------------

function GraphPanel({ runId }) {
  const graph = useRunGraph(runId)
  if (!runId) return jsx(EmptyState, { title: 'Select a run first' })
  if (graph.isLoading) return jsx(Skeleton, { className: 'h-full w-full' })
  if (!graph.data || !graph.data.nodes) return jsx(EmptyState, { title: 'No graph data' })
  return jsx(GraphView, { data: graph.data })
}

function GraphView({ data }) {
  const nodes = data.nodes || []
  const edges = data.edges || []
  // Deterministic layout — order in two rows by node id hash.
  const w = Math.max(640, nodes.length * 140)
  const h = 280
  const pos = {}
  nodes.forEach((n, i) => {
    pos[n.id] = { x: 60 + (i % 5) * 140, y: 80 + Math.floor(i / 5) * 140 }
  })
  return jsx('svg', {
    viewBox: `0 0 ${w} ${h}`,
    width: '100%',
    height: h,
    className: 'border border-(--ui-stroke-secondary) rounded bg-(--ui-bg-elevated)',
    children: jsxs(Fragment, {
      children: [
        ...edges.map((e, i) => {
          const a = pos[e.from]
          const b = pos[e.to]
          if (!a || !b) return null
          return jsx('line', {
            x1: a.x, y1: a.y, x2: b.x, y2: b.y,
            stroke: 'var(--ui-stroke-secondary)', strokeWidth: 1.5,
          }, i)
        }),
        ...nodes.map(n => {
          const p = pos[n.id]
          return jsxs('g', {
            transform: `translate(${p.x},${p.y})`,
            children: [
              jsx('rect', {
                x: -50, y: -22, width: 100, height: 44,
                rx: 6,
                fill: n.state === 'completed' ? 'var(--ui-bg-success)'
                  : n.state === 'failed' ? 'var(--ui-bg-error)'
                  : 'var(--ui-bg-base)',
                stroke: 'var(--ui-stroke-secondary)',
              }),
              jsx('text', {
                x: 0, y: -4, textAnchor: 'middle',
                fill: 'var(--ui-text-primary)', fontSize: 11,
                children: n.id,
              }),
              jsx('text', {
                x: 0, y: 12, textAnchor: 'middle',
                fill: 'var(--ui-text-tertiary)', fontSize: 9,
                children: `${n.role} · ${n.engine || 'auto'}`,
              }),
            ],
          }, n.id)
        }),
      ],
    }),
  })
}

// ---------------------------------------------------------------------------
// Timeline panel
// ---------------------------------------------------------------------------

function TimelinePanel({ runId }) {
  const sinceRef = useRef(0)
  const events = useRunEvents(runId, sinceRef)
  if (!runId) return jsx(EmptyState, { title: 'Select a run first' })
  if (events.isLoading) return jsx(Skeleton, { className: 'h-full w-full' })
  const list = events.data?.events || []
  if (list.length === 0) return jsx(EmptyState, { title: 'No events yet' })
  return jsx('div', {
    className: 'h-full overflow-auto p-4 font-mono text-xs',
    children: jsx('ul', {
      className: 'flex flex-col gap-1',
      children: list.map(e => jsxs('li', {
        className: 'flex gap-2 text-(--ui-text-secondary)',
        children: [
          jsx('span', { className: 'text-(--ui-text-quaternary)', children: '#' + e.id }),
          jsx('span', { className: 'text-(--ui-text-tertiary)', children: e.kind }),
          jsx('span', { className: 'text-(--ui-text-quaternary)', children: new Date(e.ts * 1000).toISOString().slice(11, 19) }),
        ],
      }, e.id)),
    }),
  })
}

// ---------------------------------------------------------------------------
// Office panel — Canvas2D + procedural sprites + real event movement
// ---------------------------------------------------------------------------

function OfficePanel({ runId, packId }) {
  const run = useRun(runId)
  const agents = run.data?.agents || []
  const canvasRef = useRef(null)
  const stateRef = useRef({ agents: {}, lastTick: 0 })
  const sinceRef = useRef(0)
  const events = useRunEvents(runId, sinceRef)

  // Update positions based on incoming events.
  useEffect(() => {
    const s = stateRef.current
    if (!events.data) return
    for (const e of events.data.events || []) {
      if (e.id <= sinceRef.current) continue
      sinceRef.current = Math.max(sinceRef.current, e.id)
      const agentId = e.agent_id || e.run_id
      if (!agentId) continue
      const pos = s.agents[agentId] || spawnPos(agentId)
      const kind = e.kind || e.event
      const station = stationForEvent(kind)
      pos.tx = station.x
      pos.ty = station.y
      s.agents[agentId] = pos
    }
  }, [events.data])

  // Animation loop.
  useEffect(() => {
    const c = canvasRef.current
    if (!c) return
    const ctx2d = c.getContext('2d')
    const s = stateRef.current
    let raf
    const tick = (now) => {
      const dt = Math.min(0.05, (now - (s.lastTick || now)) / 1000)
      s.lastTick = now
      for (const id in s.agents) {
        const a = s.agents[id]
        a.x += (a.tx - a.x) * Math.min(1, dt * 4)
        a.y += (a.ty - a.y) * Math.min(1, dt * 4)
      }
      draw(ctx2d, c, s, agents)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [agents])

  return jsx('div', {
    className: 'h-full p-4',
    children: jsxs('div', {
      className: 'flex h-full flex-col gap-2',
      children: [
        jsxs('div', {
          className: 'flex items-center gap-2 text-xs text-(--ui-text-tertiary)',
          children: [
            jsx('span', { children: 'Office · characters move on real events' }),
            jsx('span', { className: 'ml-auto', children: 'pack: ' + (packId || 'default') }),
          ],
        }),
        jsx('canvas', {
          ref: canvasRef,
          width: 800, height: 480,
          className: 'flex-1 w-full border border-(--ui-stroke-secondary) rounded bg-(--ui-bg-base)',
        }),
        !runId && jsx(EmptyState, { title: 'Select a run to see the office', description: 'Run a topology and watch agents move between stations.' }),
      ],
    }),
  })
}

function spawnPos(id) {
  // Spread starting agents across the floor.
  const hash = [...id].reduce((a, c) => a + c.charCodeAt(0), 0)
  const angle = (hash % 360) * Math.PI / 180
  return { x: 400 + Math.cos(angle) * 180, y: 240 + Math.sin(angle) * 100, tx: 400, ty: 240 }
}

function stationForEvent(kind) {
  if (!kind) return { x: 400, y: 240, label: 'desk' }
  const k = String(kind)
  if (k.includes('tool') && k.includes('started')) return { x: 120, y: 360, label: 'terminal' }
  if (k.includes('file')) return { x: 220, y: 380, label: 'files' }
  if (k.includes('browser') || k.includes('web')) return { x: 660, y: 360, label: 'web' }
  if (k.includes('delegat') || k.includes('agent.started')) return { x: 400, y: 100, label: 'dispatch' }
  if (k.includes('validation') || k.includes('gate')) return { x: 400, y: 420, label: 'qa' }
  if (k.includes('message')) return { x: 540, y: 200, label: 'mail' }
  if (k.includes('model.request')) return { x: 260, y: 120, label: 'thinking' }
  if (k.includes('worker.completed')) return { x: 740, y: 100, label: 'exit' }
  if (k.includes('worker.failed')) return { x: 740, y: 420, label: 'error' }
  return { x: 400, y: 240, label: 'desk' }
}

function draw(ctx2d, canvas, s, liveAgents) {
  const w = canvas.width
  const h = canvas.height
  const styles = getComputedStyle(canvas)
  const bg = styles.getPropertyValue('--ui-bg-base').trim() || '#111'
  const stroke = styles.getPropertyValue('--ui-stroke-secondary').trim() || '#444'
  const accent = styles.getPropertyValue('--ui-accent').trim() || '#4a78c8'
  const text = styles.getPropertyValue('--ui-text-primary').trim() || '#eee'
  const muted = styles.getPropertyValue('--ui-text-tertiary').trim() || '#888'

  ctx2d.fillStyle = bg
  ctx2d.fillRect(0, 0, w, h)

  // Floor
  ctx2d.strokeStyle = stroke
  ctx2d.lineWidth = 1
  ctx2d.strokeRect(20, 20, w - 40, h - 40)

  // Stations
  const stations = [
    { x: 120, y: 360, label: 'terminal' },
    { x: 220, y: 380, label: 'files' },
    { x: 660, y: 360, label: 'web' },
    { x: 400, y: 100, label: 'dispatch' },
    { x: 400, y: 420, label: 'qa' },
    { x: 540, y: 200, label: 'mail' },
    { x: 260, y: 120, label: 'thinking' },
    { x: 740, y: 100, label: 'exit' },
    { x: 740, y: 420, label: 'error' },
  ]
  for (const s of stations) {
    ctx2d.fillStyle = stroke
    ctx2d.beginPath(); ctx2d.arc(s.x, s.y, 18, 0, Math.PI * 2); ctx2d.fill()
    ctx2d.fillStyle = text
    ctx2d.font = '10px monospace'
    ctx2d.textAlign = 'center'
    ctx2d.fillText(s.label, s.x, s.y + 32)
  }

  // Characters — procedural simple silhouette.
  const roles = ['architect', 'builder', 'reviewer', 'validator']
  let i = 0
  for (const id in s.agents) {
    const a = s.agents[id]
    const role = roles[i++ % roles.length]
    const colors = {
      architect: { body: '#4a78c8', head: '#f0c8a0' },
      builder: { body: '#d97a3c', head: '#f0c8a0' },
      reviewer: { body: '#8a4ac8', head: '#f0c8a0' },
      validator: { body: '#3ca870', head: '#f0c8a0' },
    }[role]
    // Shadow
    ctx2d.fillStyle = '#0006'
    ctx2d.beginPath(); ctx2d.ellipse(a.x, a.y + 16, 8, 2, 0, 0, Math.PI * 2); ctx2d.fill()
    // Body
    ctx2d.fillStyle = colors.body
    ctx2d.fillRect(a.x - 6, a.y - 12, 12, 16)
    // Head
    ctx2d.fillStyle = colors.head
    ctx2d.fillRect(a.x - 4, a.y - 22, 8, 10)
    // Label
    ctx2d.fillStyle = text
    ctx2d.font = '9px monospace'
    ctx2d.textAlign = 'center'
    ctx2d.fillText(role, a.x, a.y - 28)
    ctx2d.fillStyle = muted
    ctx2d.fillText(id.slice(-6), a.x, a.y + 32)
  }

  // Live agent state — small overlay if we have live agents.
  ctx2d.fillStyle = text
  ctx2d.font = '11px monospace'
  ctx2d.textAlign = 'left'
  ctx2d.fillText(`agents: ${liveAgents.length || Object.keys(s.agents).length}`, 28, 40)
  ctx2d.fillStyle = muted
  ctx2d.fillText(`acc: ${accent}`, 28, 56)
}

// ---------------------------------------------------------------------------
// Plugins panel
// ---------------------------------------------------------------------------

function PluginsPanel({}) {
  const plugins = usePlugins()
  if (plugins.isLoading) return jsx(Skeleton, { className: 'h-32 w-full m-4' })
  const list = plugins.data?.plugins || []
  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsx('div', {
        className: 'border-b border-(--ui-stroke-secondary) px-4 py-2 text-(--ui-text-secondary)',
        children: jsxs('div', {
          className: 'flex items-center gap-2',
          children: [
            jsx('strong', { className: 'text-(--ui-text-primary)', children: 'Plugin Lab' }),
            jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: 'inspect · create · validate · activate · rollback' }),
            jsx('span', { className: 'ml-auto' }),
            jsx(Button, {
              onClick: () => {
                const manifest = { id: 'sample', version: '0.0.1', provides: ['capability.test'] }
                const code = 'def hello():\n    return "ok"\n'
                rest('/plugin-lab/create', { method: 'POST', body: { plugin_id: 'sample', manifest, code } })
                  .then(r => rest('/plugin-lab/validate', { method: 'POST', body: { plugin_id: 'sample', version: r.version } }))
                  .then(() => queryClient.invalidateQueries({ queryKey: [ID, 'plugins'] }))
              },
              children: 'create sample',
            }),
          ],
        }),
      }),
      jsx('div', {
        className: 'flex-1 min-h-0 overflow-auto p-4',
        children: list.length === 0
          ? jsx(EmptyState, { title: 'No generated plugins', description: 'Create one with the button above.' })
          : jsx('div', {
              className: 'grid gap-3',
              children: list.map(p =>
                jsxs('div', {
                  className: 'rounded border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) p-3',
                  children: [
                    jsxs('div', { className: 'flex items-center gap-2', children: [
                      jsx('strong', { className: 'text-(--ui-text-primary)', children: p.id }),
                      jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: `${p.versions.length} version(s)` }),
                    ]}),
                    ...p.versions.map(v =>
                      jsxs('div', {
                        className: 'mt-2 flex items-center gap-2 text-xs text-(--ui-text-secondary)',
                        children: [
                          jsx('span', { className: 'font-mono', children: v.version }),
                          jsx(StatusDot, { variant: v.active ? 'active' : 'neutral' }),
                          jsx('span', { className: 'text-(--ui-text-tertiary)', children: v.active ? 'current' : '—' }),
                          jsx('span', { className: 'ml-auto' }),
                          jsx(Button, {
                            variant: 'ghost',
                            disabled: !v.validation,
                            onClick: () => rest('/plugin-lab/activate', { method: 'POST', body: { plugin_id: p.id, version: v.version } })
                              .then(() => queryClient.invalidateQueries({ queryKey: [ID, 'plugins'] })),
                            children: 'activate',
                          }),
                          jsx(Button, {
                            variant: 'ghost',
                            onClick: () => rest('/plugin-lab/rollback', { method: 'POST', body: { plugin_id: p.id } })
                              .then(() => queryClient.invalidateQueries({ queryKey: [ID, 'plugins'] })),
                            children: 'rollback',
                          }),
                        ],
                      }, v.version),
                    ),
                  ],
                }, p.id),
              ),
            }),
      }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Characters panel
// ---------------------------------------------------------------------------

function CharactersPanel({}) {
  const packs = useCharacterPacks()
  const assignments = useQuery({
    queryKey: [ID, 'character-assignments'],
    queryFn: () => rest('/character-assignments'),
    refetchInterval: 8000,
  })
  const list = packs.data?.packs || []
  const active = packs.data?.active
  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsx('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-2 text-(--ui-text-secondary)',
        children: jsxs('div', {
          className: 'flex items-center gap-2',
          children: [
            jsx('strong', { className: 'text-(--ui-text-primary)', children: 'Character Packs' }),
            jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: `active: ${active || '—'}` }),
            jsx('span', { className: 'ml-auto' }),
            jsx(Button, {
              onClick: () => rest('/character-packs/reload', { method: 'POST' })
                .then(() => queryClient.invalidateQueries({ queryKey: [ID, 'characters'] })),
              children: 'reload',
            }),
          ],
        }),
      }),
      jsx('div', {
        className: 'flex-1 min-h-0 overflow-auto p-4 grid gap-3',
        children: list.map(p =>
          jsxs('div', {
            className: cn(
              'rounded border p-3',
              p.id === active ? 'border-(--ui-accent) bg-(--ui-bg-elevated)' : 'border-(--ui-stroke-secondary) bg-(--ui-bg-base)'
            ),
            children: [
              jsxs('div', { className: 'flex items-center gap-2', children: [
                jsx('strong', { className: 'text-(--ui-text-primary)', children: p.name }),
                jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: p.id }),
                jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: `v${p.version}` }),
                jsx('span', { className: 'ml-auto' }),
                jsx(Button, {
                  disabled: p.id === active,
                  onClick: () => rest('/character-packs/' + p.id + '/activate', { method: 'POST' })
                    .then(() => queryClient.invalidateQueries({ queryKey: [ID, 'characters'] })),
                  children: p.id === active ? 'active' : 'activate',
                }),
              ]}),
              jsx('div', {
                className: 'mt-2 text-xs text-(--ui-text-tertiary)',
                children: 'characters: ' + p.characters.map(c => c.id).join(', '),
              }),
            ],
          }, p.id),
        ),
      }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Sidebar nav + command palette + status bar
// ---------------------------------------------------------------------------

export default {
  id: ID,
  name: 'Meta-Harness',
  description: 'Self-extensible multi-agent meta-harness.',
  defaultEnabled: true,
  register(ctx) {
    // Sidebar entry
    ctx.register({
      id: ID + ':nav',
      area: SIDEBAR_NAV_AREA,
      order: 80,
      data: {
        path: '/meta-harness',
        label: 'Meta-Harness',
        codicon: 'rocket',
      },
    })

    // Route
    ctx.register({
      id: ID + ':route',
      area: ROUTES_AREA,
      data: { path: '/meta-harness' },
      render: () => jsx(CommandCenter, {}),
    })

    // Status bar chip
    ctx.register({
      id: ID + ':chip',
      area: 'statusBar.right',
      order: 90,
      render: () => jsx(StatusChip, {}),
    })

    // Command palette
    ctx.register({
      id: ID + ':open',
      area: PALETTE_AREA,
      data: {
        label: 'Open Meta-Harness',
        keywords: ['harness', 'meta', 'agent', 'orchestration'],
        perform: () => host.navigate('/meta-harness'),
      },
    })
    ctx.register({
      id: ID + ':reload-packs',
      area: PALETTE_AREA,
      data: {
        label: 'Reload character packs',
        keywords: ['character', 'pack', 'sprite'],
        perform: () => rest('/character-packs/reload', { method: 'POST' })
          .then(() => host.notify({ kind: 'info', message: 'Character packs reloaded.' })),
      },
    })
    ctx.register({
      id: ID + ':reload-plugins',
      area: PALETTE_AREA,
      data: {
        label: 'Reload Meta-Harness backend',
        keywords: ['meta-harness', 'reload'],
        perform: () => host.notify({ kind: 'info', message: 'Use ⌘K → Reload desktop plugins to hot-reload the JS side.' }),
      },
    })
  },
}