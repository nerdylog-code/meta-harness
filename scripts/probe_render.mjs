// Render-test: mount the plugin's CommandCenter under React 18 + jsdom.
// Catches runtime errors that 'check_plugin_load.py' (which only loads
// the module) misses — undefined imports used inside JSX, etc.
//
// Read plugin path from argv[2]. Resolves node_modules relative to this
// script (so cwd doesn't matter).

import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { JSDOM } from 'jsdom'

const pluginPath = process.argv[2]
if (!pluginPath) {
  console.error('usage: node probe_render.mjs <path-to-plugin.js>')
  process.exit(2)
}

// Resolve node_modules sibling to THIS script (scripts/node_modules).
const HERE = dirname(fileURLToPath(import.meta.url))
const NM = join(HERE, 'node_modules')

// Set up a DOM before React loads.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true,
})
globalThis.window = dom.window
globalThis.document = dom.window.document
try { Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true }) } catch {}
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.Element = dom.window.Element
globalThis.Node = dom.window.Node
globalThis.MutationObserver = dom.window.MutationObserver
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame || ((cb) => setTimeout(cb, 16))
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame || ((id) => clearTimeout(id))
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window)

// Now load React.
const reactPath = pathToFileURL(join(NM, 'react', 'index.js')).href
const jsxRuntimePath = pathToFileURL(join(NM, 'react', 'jsx-runtime.js')).href
const jsxDevRuntimePath = pathToFileURL(join(NM, 'react', 'jsx-dev-runtime.js')).href
const React = (await import(reactPath)).default
const { jsx, jsxs, Fragment } = await import(jsxRuntimePath)
const ReactDOM = (await import(pathToFileURL(join(NM, 'react-dom', 'client.js')).href)).default

// Build SDK shims.
const hostStub = {
  state: { gateway: 'fake', model: 'minimax-test', profile: 'default',
           viewport: { x:0,y:0,width:1280,height:800 }, focusedSessionProfile: null },
  notify: () => {}, navigate: () => {}, request: () => ({}),
  onEvent: () => () => {}, logs: () => {}, status: () => ({}),
}
const queryClientStub = { invalidateQueries: () => {} }
const noop = () => {}

const mkStub = (tag) => (p) => React.createElement(tag, p, p?.children)
const sdk = {
  host: hostStub,
  cn: (...xs) => xs.filter(Boolean).join(' '),
  Button: mkStub('button'),
  EmptyState: (p) => React.createElement('div', { className: 'empty' }, p?.children ?? p?.title ?? ''),
  ErrorState: (p) => React.createElement('div', { className: 'error' }, p?.title ?? ''),
  GlyphSpinner: () => React.createElement('span', null, '...'),
  PALETTE_AREA: 'PALETTE_AREA',
  ROUTES_AREA: 'ROUTES_AREA',
  SIDEBAR_NAV_AREA: 'SIDEBAR_NAV_AREA',
  Skeleton: mkStub('div'),
  StatusDot: () => React.createElement('span', { className: 'dot' }),
  Tabs: (p) => React.createElement('div', null, p.children),
  TabsList: (p) => React.createElement('div', { className: 'tabs-list' }, p.children),
  TabsTrigger: (p) => React.createElement('button', { onClick: () => p.onValueChange?.(p.value) }, p.children),
  Textarea: mkStub('textarea'),
  queryClient: queryClientStub,
  useMutation: () => ({ mutate: noop, isPending: false, error: null, data: null }),
  useQuery: () => ({ data: null, isLoading: false, error: null }),
}
sdk.useState = React.useState
sdk.useEffect = React.useEffect
sdk.useRef = React.useRef

// Write shim + load plugin.
const tmp = mkdtempSync(join(tmpdir(), 'mh-render-'))

const sdkPath = join(tmp, '__sdk.mjs')
const sdkShim = [
  '// SDK shim — minimal but realistic for our plugin. The Hermes SDK',
  '// exports a real `cn` utility, but we reproduce the same shape with a',
  '// tailwind-style class joiner so render behaviour matches production.',
  'export const cn = (...xs) => xs.filter(Boolean).join(" ")',
  '',
  'const exports_obj = {',
  '  host: ' + JSON.stringify(hostStub) + ',',
  '  Button: "Button", EmptyState: "EmptyState", ErrorState: "ErrorState",',
  '  GlyphSpinner: "GlyphSpinner",',
  '  PALETTE_AREA: "PALETTE_AREA", ROUTES_AREA: "ROUTES_AREA", SIDEBAR_NAV_AREA: "SIDEBAR_NAV_AREA",',
  '  Skeleton: "Skeleton", StatusDot: "StatusDot", Tabs: "Tabs",',
  '  TabsList: "TabsList", TabsTrigger: "TabsTrigger", Textarea: "Textarea",',
  '  queryClient: ' + JSON.stringify(queryClientStub) + ',',
  '  useMutation: () => ({ mutate: noop, isPending: false, error: null, data: null }),',
  '  useQuery: () => ({ data: null, isLoading: false, error: null }),',
  '};',
  'export default exports_obj;',
  'export const { host, Button, EmptyState, ErrorState, GlyphSpinner,',
  '  PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, Skeleton, StatusDot,',
  '  Tabs, TabsList, TabsTrigger, Textarea, queryClient, useMutation, useQuery } = exports_obj;',
  '',
].join('\n')
writeFileSync(sdkPath, sdkShim)

const reactShimPath = join(tmp, '__react.mjs')
writeFileSync(reactShimPath, `
const R = await import(${JSON.stringify(reactPath)});
export default R.default;
export const useState = R.useState;
export const useEffect = R.useEffect;
export const useRef = R.useRef;
export const useMemo = R.useMemo;
`)

const jsxShimPath = join(tmp, '__jsx.mjs')
writeFileSync(jsxShimPath, `
const J = await import(${JSON.stringify(jsxRuntimePath)});
export const jsx = J.jsx;
export const jsxs = J.jsxs;
export const Fragment = J.Fragment;
`)

const pluginSrc = readFileSync(pluginPath, 'utf8')
const rewritten = pluginSrc
  .replaceAll('@hermes/plugin-sdk', pathToFileURL(sdkPath).href)
  .replaceAll(`from 'react/jsx-runtime'`, `from ${JSON.stringify(pathToFileURL(jsxShimPath).href)}`)
  .replaceAll(`from 'react'`, `from ${JSON.stringify(pathToFileURL(reactShimPath).href)}`)

const pluginOut = join(tmp, 'plugin.mjs')
writeFileSync(pluginOut, rewritten, {encoding: 'utf8'})

// Fake context that captures contributions.
const reg = []
let plugin
try {
  const mod = await import(pathToFileURL(pluginOut).href)
  plugin = mod.default
  // We can't easily pass ctx because the plugin reads ctx at register() time.
  // Build a fake ctx with ctx.rest that returns a fake status.
  const fakeCtx = {
    register: (c) => reg.push(c),
    storage: { get: () => null, set: () => {}, remove: () => {} },
    rest: async () => ({
      name: 'meta-harness', version: '0.1.0',
      engines: { hermes: { available: true, default_model: 'minimax-m3' }, pi: { available: true, default_model: null } },
      active_pack: 'default',
      topologies: ['solo', 'architect-builder', 'fusion', 'gate-build', 'visual-build-review'],
      character_packs: ['default'],
    }),
  }
  plugin.register(fakeCtx)
} catch (e) {
  console.error('PLUGIN REGISTER FAILED:', e.message)
  console.error(e.stack)
  process.exit(2)
}

console.log(`register() ran; ${reg.length} contributions:`)
for (const c of reg) console.log(`  - ${c.id} area=${c.area}`)

// Find the ROUTES_AREA contribution and mount it.
const route = reg.find(c => c.area === 'ROUTES_AREA')
if (!route || typeof route.render !== 'function') {
  console.error('FAIL: no ROUTES_AREA contribution with render()')
  process.exit(3)
}

const root = dom.window.document.getElementById('root')
try {
  await ReactDOM.createRoot(root).render(React.createElement(route.render))
  // Wait a tick for effects.
  await new Promise(r => setTimeout(r, 200))
  const html = root.innerHTML
  console.log('=== rendered HTML (first 600 chars) ===')
  console.log(html.slice(0, 600))
  console.log('=== render length:', html.length, '===')
  if (html.length < 50) {
    console.error('FAIL: rendered output suspiciously short')
    process.exit(4)
  }
  console.log('PASS: render produced HTML, no exceptions.')
  // The CommandCenter schedules a setInterval for status polling; that
  // keeps the event loop alive past our render check. Force-exit so the
  // test harness can complete.
  process.exit(0)
} catch (e) {
  console.error('RENDER FAILED:', e.message)
  console.error(e.stack)
  process.exit(5)
} finally {
  rmSync(tmp, { recursive: true, force: true })
}