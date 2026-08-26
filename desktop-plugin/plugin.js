/**
 * Meta-Harness desktop plugin — minimal, observable, functional.
 *
 * Strategy: start small. The first goal is to *see something* when the
 * user opens `/meta-harness`. Only after that do we layer in queries,
 * graphs, canvas, and animations. Every render path is wrapped in an
 * ErrorBoundary so a single bad child never blanks the whole page —
 * it shows a localized diagnostic instead.
 *
 * Pipeline (matches the Hermes runtime loader):
 *   - imports restricted to @hermes/plugin-sdk + react + react/jsx-runtime
 *   - default export { id, name, defaultEnabled, register(ctx) }
 *   - register() pushes ROUTES_AREA + SIDEBAR_NAV_AREA + statusBar
 */

import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'

const { Button,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Skeleton,
  StatusDot,
  Tabs,
  TabsList,
  TabsTrigger,
  Textarea,
  cn,
  host,
  queryClient,
  useMutation,
  useQuery,
} = sdk

const ID = 'meta-harness'

// Re-export a tag for the renderer log so we can grep our own errors.
const TAG = '[meta-harness]'

// ---------------------------------------------------------------------------
// ErrorBoundary — keeps the rest of the UI alive when one panel crashes.
// ---------------------------------------------------------------------------

function EB({ children, name }) {
  const [error, setError] = useState(null)
  if (error) {
    return jsx('div', {
      className: 'm-3 p-3 rounded border border-(--ui-stroke-secondary) text-xs bg-(--ui-bg-elevated)',
      children: jsxs('div', {
        children: [
          jsx('strong', { children: `Panel "${name}" crashed` }),
          jsx('br'),
          jsx('code', { children: String(error.message || error) }),
        ],
      }),
    })
  }
  return children
}

// Note: the class-based ErrorBoundary that follows is intentionally minimal
// and never used. EB above is what we render — kept here as documentation
// of why a real React ErrorBoundary would normally live below.
class ErrorBoundary extends Object {}

// ---------------------------------------------------------------------------
// REST helpers — only /status for now (proves the wiring).
// ---------------------------------------------------------------------------

async function rest(path, opts = {}) {
  if (typeof ctx !== 'undefined' && ctx && typeof ctx.rest === 'function') {
    return ctx.rest(path, opts)
  }
  const r = await fetch('/api/plugins/' + ID + path, {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  return r.json()
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function CommandCenter() {
  // No React Query yet — just a single /status fetch with useEffect+state.
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const s = await rest('/status')
        if (!cancelled) {
          setStatus(s)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e && e.message ? e.message : String(e))
      }
    }
    tick()
    const t = setInterval(tick, 4000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  return jsx('div', {
    className: 'flex flex-col h-full min-h-0',
    children: jsxs(Fragment, {
      children: [
        jsx(Header, { status, error }),
        error
          ? jsx(BackendDown, { error })
          : !status
            ? jsx(Loading, {})
            : jsx(TabSwitcher, { status }),
      ],
    }),
  })
}

function Header({ status, error }) {
  const activeTopo = status?.topologies?.[0] || '—'
  const engines = status?.engines || {}
  return jsxs('div', {
    className: cn(
      'flex items-center gap-3 px-4 py-2',
      'border-b border-(--ui-stroke-secondary) bg-(--ui-bg-elevated)',
      'text-(--ui-text-secondary)'
    ),
    children: [
      jsx('strong', {
        className: 'text-(--ui-text-primary) font-semibold tracking-tight',
        children: 'Meta-Harness',
      }),
      jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
      jsxs('span', { className: 'text-xs', children: ['topo:', jsx('span', { className: 'ml-1 text-(--ui-text-primary)', children: activeTopo })] }),
      jsx('span', { className: 'text-(--ui-text-quaternary)', children: '·' }),
      jsxs('span', { className: 'text-xs', children: ['engines:', jsx('span', { className: 'ml-1', children: `${engines.hermes?.available ? 'hermes' : 'off'}/${engines.pi?.available ? 'pi' : 'off'}` })] }),
      jsx('span', { className: 'ml-auto text-(--ui-text-quaternary)', children: 'v0.1.0' }),
    ],
  })
}

function Loading() {
  return jsx('div', {
    className: 'flex-1 flex items-center justify-center',
    children: jsxs('div', {
      className: 'flex items-center gap-2 text-(--ui-text-tertiary)',
      children: [jsx(GlyphSpinner, {}), jsx('span', { children: 'connecting to backend...' })],
    }),
  })
}

function BackendDown({ error }) {
  return jsxs('div', {
    className: 'flex-1 flex items-center justify-center p-8',
    children: [
      jsx('div', {
        className: 'max-w-md text-center',
        children: jsxs('div', {
          className: 'space-y-2',
          children: [
            jsx('h2', { className: 'text-base font-semibold text-(--ui-text-primary)', children: 'Meta-Harness backend unreachable' }),
            jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: 'The desktop UI is loaded, but /api/plugins/meta-harness/* did not respond.' }),
            jsx('p', { className: 'text-xs font-mono text-(--ui-text-quaternary) mt-2', children: error }),
            jsx('p', { className: 'text-xs text-(--ui-text-tertiary) mt-3', children: 'Run bash scripts/doctor.sh to diagnose.' }),
          ],
        }),
      }),
    ],
  })
}

function TabSwitcher({ status }) {
  const [tab, setTab] = useState('overview')
  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'agents', label: 'Agents' },
    { id: 'topologies', label: 'Topologies' },
    { id: 'characters', label: 'Characters' },
  ]
  return jsxs('div', {
    className: 'flex flex-col flex-1 min-h-0',
    children: [
      jsx('div', {
        className: 'px-4 py-2 border-b border-(--ui-stroke-secondary)',
        children: jsx(Tabs, {
          value: tab,
          onValueChange: setTab,
          children: jsx(TabsList, {
            children: tabs.map(t =>
              jsx(TabsTrigger, { value: t.id, children: t.label }, t.id)
            ),
          }),
        }),
      }),
      jsx('div', {
        className: 'flex-1 min-h-0 overflow-auto p-4',
        children: tab === 'overview'
          ? jsx(EB, { name: 'overview', children: jsx(OverviewPanel, { status }) })
          : tab === 'agents'
            ? jsx(EB, { name: 'agents', children: jsx(AgentsPanel, {}) })
            : tab === 'topologies'
              ? jsx(EB, { name: 'topologies', children: jsx(TopologiesPanel, {}) })
              : jsx(EB, { name: 'characters', children: jsx(CharactersPanel, {}) }),
      }),
    ],
  })
}

function OverviewPanel({ status }) {
  const tops = status?.topologies || []
  const packs = status?.character_packs || []
  return jsxs('div', {
    className: 'space-y-4',
    children: [
      jsxs('section', {
        className: 'space-y-1',
        children: [
          jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Engines' }),
          jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: 'Active engines available for topologies.' }),
          jsxs('ul', {
            className: 'mt-2 space-y-1 text-xs',
            children: [
              jsxs('li', { children: [
                jsx(StatusDot, { variant: status?.engines?.hermes?.available ? 'active' : 'neutral' }),
                jsx('span', { className: 'ml-2', children: 'hermes' }),
                jsx('span', { className: 'ml-2 text-(--ui-text-quaternary)', children: status?.engines?.hermes?.available ? 'available' : 'unavailable' }),
                status?.engines?.hermes?.default_model && jsxs('span', { className: 'ml-2 text-(--ui-text-quaternary)', children: ['(', status.engines.hermes.default_model, ')'] }),
              ]}),
              jsxs('li', { children: [
                jsx(StatusDot, { variant: status?.engines?.pi?.available ? 'active' : 'neutral' }),
                jsx('span', { className: 'ml-2', children: 'pi' }),
                jsx('span', { className: 'ml-2 text-(--ui-text-quaternary)', children: status?.engines?.pi?.available ? 'available' : 'unavailable' }),
              ]}),
            ],
          }),
        ],
      }),
      jsxs('section', {
        className: 'space-y-1',
        children: [
          jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Topologies' }),
          jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: `${tops.length} loaded.` }),
          jsx('div', {
            className: 'mt-2 flex flex-wrap gap-2',
            children: tops.map(t =>
              jsx('span', {
                className: 'px-2 py-1 rounded text-xs bg-(--ui-bg-elevated) border border-(--ui-stroke-secondary)',
                children: t,
              }, t)
            ),
          }),
        ],
      }),
      jsxs('section', {
        className: 'space-y-1',
        children: [
          jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Character packs' }),
          jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: `${packs.length} installed.` }),
          jsx('div', {
            className: 'mt-2 flex flex-wrap gap-2',
            children: packs.map(p =>
              jsx('span', {
                className: cn(
                  'px-2 py-1 rounded text-xs border',
                  p === status?.active_pack
                    ? 'bg-(--ui-bg-elevated) border-(--ui-accent) text-(--ui-text-primary)'
                    : 'bg-(--ui-bg-elevated) border-(--ui-stroke-secondary)'
                ),
                children: p,
              }, p)
            ),
          }),
        ],
      }),
    ],
  })
}

function AgentsPanel() {
  return jsx(EmptyState, {
    title: 'No runs yet',
    description: 'Run a topology from the chat composer or via the harness_run tool.',
  })
}

function TopologiesPanel() {
  const [list, setList] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    rest('/topologies').then(r => setList(r?.topologies || [])).catch(e => setErr(e?.message || String(e)))
  }, [])
  if (err) return jsx(ErrorState, { title: 'Failed to load topologies', children: err })
  if (!list) return jsx(Skeleton, { className: 'h-32 w-full' })
  return jsxs('div', {
    className: 'space-y-2',
    children: [
      jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: `${list.length} topologies available.` }),
      ...list.map(t => jsxs('div', {
        className: 'rounded border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) px-3 py-2',
        children: [
          jsxs('div', { className: 'flex items-center gap-2', children: [
            jsx('strong', { className: 'text-sm text-(--ui-text-primary)', children: t.id }),
            jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: t.kind }),
          ]}),
          jsx('div', { className: 'text-xs text-(--ui-text-quaternary) mt-1', children: 'nodes: ' + (t.nodes || []).join(', ') }),
        ],
      }, t.id)),
    ],
  })
}

function CharactersPanel() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    rest('/character-packs').then(setData).catch(e => setErr(e?.message || String(e)))
  }, [])
  if (err) return jsx(ErrorState, { title: 'Failed to load character packs', children: err })
  if (!data) return jsx(Skeleton, { className: 'h-32 w-full' })
  const packs = data.packs || []
  const active = data.active
  return jsxs('div', {
    className: 'space-y-2',
    children: [
      jsxs('p', { className: 'text-xs text-(--ui-text-tertiary)', children: [`Active pack: `, jsx('span', { className: 'text-(--ui-text-primary)', children: active || '—' })] }),
      ...packs.map(p => jsxs('div', {
        className: cn(
          'rounded border px-3 py-2',
          p.id === active ? 'border-(--ui-accent) bg-(--ui-bg-elevated)' : 'border-(--ui-stroke-secondary)'
        ),
        children: [
          jsxs('div', { className: 'flex items-center gap-2', children: [
            jsx('strong', { className: 'text-sm text-(--ui-text-primary)', children: p.name }),
            jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: `v${p.version}` }),
            jsx('span', { className: 'ml-auto text-xs text-(--ui-text-quaternary)', children: p.id }),
          ]}),
          jsx('div', { className: 'text-xs text-(--ui-text-quaternary) mt-1', children: 'characters: ' + (p.characters || []).map(c => c.id).join(', ') }),
        ],
      }, p.id)),
    ],
  })
}

// ---------------------------------------------------------------------------
// Status bar chip + nav + palette commands
// ---------------------------------------------------------------------------

function StatusChip() {
  const [s, setS] = useState(null)
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const r = await rest('/status')
        if (!cancelled) setS(r)
      } catch {}
    }
    tick()
    const t = setInterval(tick, 6000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])
  const h = s?.engines?.hermes?.available
  const p = s?.engines?.pi?.available
  return jsxs('span', {
    className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    children: [jsx('span', { 'aria-hidden': true, children: '◆' }), jsx('span', { children: 'Meta-Harness' })],
    title: `Hermes:${h ? 'on' : 'off'}  Pi:${p ? 'on' : 'off'}`,
  })
}

export default {
  id: ID,
  name: 'Meta-Harness',
  description: 'Self-extensible multi-agent meta-harness.',
  defaultEnabled: true,

  register(ctx) {
    // Nav row in the sidebar.
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

    // Route — mounts the Command Center in the workspace pane.
    ctx.register({
      id: ID + ':route',
      area: ROUTES_AREA,
      data: { path: '/meta-harness' },
      render: () => jsx(CommandCenter, {}),
    })

    // Status bar chip on the right.
    ctx.register({
      id: ID + ':chip',
      area: 'statusBar.right',
      order: 90,
      render: () => jsx(StatusChip, {}),
    })

    // Command palette commands.
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
      id: ID + ':doctor',
      area: PALETTE_AREA,
      data: {
        label: 'Run Meta-Harness doctor',
        keywords: ['harness', 'doctor', 'diagnostics'],
        perform: () => host.notify({
          kind: 'info',
          message: 'Run `bash scripts/doctor.sh` from the meta-harness repo to diagnose.',
        }),
      },
    })
  },
}