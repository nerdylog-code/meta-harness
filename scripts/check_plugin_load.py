#!/usr/bin/env python3
"""Smoke-test the desktop plugin the way Hermes Desktop actually loads it.

Reproduces the runtime loader pipeline:
  1. Read plugin.js as text.
  2. Rewrite bare specifiers (@hermes/plugin-sdk, react, react/jsx-runtime)
     to file:// URLs pointing at minimal in shims.
  3. Dynamic-import the rewritten source via Node ESM.
  4. Validate the default export and call register() with a fake ctx.
  5. Report what was registered.

Usage:
    python scripts/check_plugin_load.py [plugin.js path]
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path


SDK_JS = r"""
const sdk = {
  host: { state: { gateway: 'fake', model: 'minimax-test',
                   profile: 'default', viewport: { x:0,y:0,width:1280,height:800 },
                   focusedSessionProfile: null },
           notify: () => {}, navigate: () => {},
           request: () => ({}), onEvent: () => () => {},
           logs: () => {}, status: () => ({}) },
  cn: (...xs) => xs.filter(Boolean).join(' '),
  Button: 'Button', EmptyState: 'EmptyState', ErrorState: 'ErrorState',
  GlyphSpinner: 'GlyphSpinner', PALETTE_AREA: 'PALETTE_AREA',
  queryClient: { invalidateQueries: () => {} },
  ROUTES_AREA: 'ROUTES_AREA', SIDEBAR_NAV_AREA: 'SIDEBAR_NAV_AREA',
  Skeleton: 'Skeleton', StatusDot: 'StatusDot',
  Tabs: 'Tabs', TabsList: 'TabsList', TabsTrigger: 'TabsTrigger',
  Textarea: 'Textarea',
  Select: 'Select', SelectContent: 'SelectContent', SelectItem: 'SelectItem',
  SelectTrigger: 'SelectTrigger', SelectValue: 'SelectValue',
  Tip: 'Tip',
  useMutation: () => ({ mutate: () => {}, isPending: false, error: null, data: null }),
  useQuery: () => ({ data: null, isLoading: false, error: null }),
};
export default sdk;
export const { host, cn, Button, EmptyState, ErrorState, GlyphSpinner,
  PALETTE_AREA, queryClient, ROUTES_AREA, SIDEBAR_NAV_AREA,
  Skeleton, StatusDot, Tabs, TabsList, TabsTrigger,
  Textarea, Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue, Tip, useMutation, useQuery } = sdk;
"""

REACT_JS = r"""
const React = { useState: (i) => [i, () => {}], useEffect: () => {},
                useRef: (i) => ({ current: i }), useMemo: (fn) => fn() };
export default React;
export const { useState, useEffect, useRef, useMemo } = React;
"""

JSX_JS = r"""
export const jsx = (type, props) => ({ type, props });
export const jsxs = (type, props, key) => ({ type, props, key });
export const Fragment = 'Fragment';
"""


def main():
    if len(sys.argv) >= 2:
        plugin_path = Path(sys.argv[1])
    else:
        plugin_path = Path(__file__).resolve().parent.parent / "desktop-plugin" / "plugin.js"
    if not plugin_path.exists():
        print(f"FAIL: {plugin_path} not found")
        return 1

    tmp = Path(tempfile.gettempdir())

    def shim_url(name, code):
        p = tmp / name
        p.write_text(code, encoding="utf-8")
        ap = str(p.resolve()).replace("\\", "/")
        return "file:///" + ap.lstrip("/")

    shim_map = {
        "@hermes/plugin-sdk": shim_url("__mh_sdk_shim.mjs", SDK_JS),
        "react": shim_url("__mh_react_shim.mjs", REACT_JS),
        "react/jsx-runtime": shim_url("__mh_jsx_shim.mjs", JSX_JS),
    }

    source = plugin_path.read_text(encoding="utf-8")
    spec_re = re.compile(r"(from\s*|import\s*\(\s*|import\s+)(['\"])([^'\"]+)\2")
    rewritten = spec_re.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{shim_map.get(m.group(3), m.group(3))}{m.group(2)}",
        source,
    )

    # Also reject unsupported bare specifiers the way the Hermes runtime
    # loader does — fail fast with a clear message.
    SUPPORTED = set(shim_map.keys()) | {"./", "../", "node:"}
    bad = sorted({
        m.group(3)
        for m in re.finditer(r"(?:from\s+|import\s*\(\s*|import\s+)(['\"])([^'\"]+)\2", source)
        if not (m.group(2).startswith("./") or m.group(2).startswith("../")
                or m.group(2).startswith("node:") or m.group(2) in shim_map)
    })
    if bad:
        print(f"FAIL: unsupported bare specifiers: {bad}")
        return 2

    plugin_file = tmp / "mh_plugin_rewritten.mjs"
    plugin_file.write_text(rewritten, encoding="utf-8")
    plugin_url = "file:///" + str(plugin_file.resolve()).replace("\\", "/").lstrip("/")

    probe = tmp / "mh_probe.mjs"
    probe.write_text(f"""
const pluginUrl = {plugin_url!r};
(async () => {{
  const mod = await import(pluginUrl);
  const p = mod.default;
  if (!p || typeof p !== 'object') {{
    console.error('FAIL: no default export object');
    process.exit(3);
  }}
  if (typeof p.register !== 'function') {{
    console.error('FAIL: default export has no register(ctx)');
    process.exit(4);
  }}
  const reg = [];
  const ctx = {{
    register: (contrib) => reg.push(contrib),
    storage: {{ get: () => null, set: () => {{}}, remove: () => {{}} }},
  }};
  p.register(ctx);
  console.log('PASS id=' + p.id + ' contributions=' + reg.length);
  for (const c of reg) {{
    console.log('  - ' + c.id + ' area=' + c.area);
  }}
}})().catch(e => {{
  console.error('FAIL:', e && e.message ? e.message : e);
  if (e && e.stack) console.error(e.stack);
  process.exit(5);
}});
""", encoding="utf-8")

    try:
        rc = asyncio.run(_run(probe))
    finally:
        plugin_file.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)
    return rc


async def _run(probe):
    proc = await asyncio.create_subprocess_exec(
        "node", str(probe),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    sys.stdout.write(out.decode())
    sys.stderr.write(err.decode())
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())