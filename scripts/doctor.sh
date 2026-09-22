#!/usr/bin/env bash
# Meta-Harness doctor — checks installation health.
# Usage: bash scripts/doctor.sh [hermes-home]
# Returns: 0 = PASS, 1 = FAIL, 2 = WARN.

set -uo pipefail

if [[ $# -ge 1 ]]; then
  HERMES_HOME="${1%/}"
elif [[ -n "${HERMES_HOME:-}" ]]; then
  HERMES_HOME="${HERMES_HOME%/}"
else
  HERMES_HOME="$HOME/.hermes"
fi

# Keep relative diagnostic targets stable when the script changes directory for probes.
if [[ -d "$HERMES_HOME" ]]; then
  HERMES_HOME="$(cd "$HERMES_HOME" && pwd)"
fi

fail=0
warn=0

echo "Meta-Harness doctor"
echo "  HERMES_HOME: $HERMES_HOME"
echo

# Hermes install
if [[ -d "$HERMES_HOME" ]]; then
  echo "[PASS] Hermes Home exists"
else
  echo "[FAIL] Hermes Home missing"
  fail=$((fail+1))
fi

# Hermes version
if command -v hermes >/dev/null 2>&1; then
  v="$(hermes --version 2>&1 | head -n1)"
  echo "[PASS] Hermes CLI: $v"
else
  echo "[WARN] Hermes CLI not on PATH"
  warn=$((warn+1))
fi

# Backend plugin present
if [[ -d "$HERMES_HOME/plugins/meta-harness" ]]; then
  echo "[PASS] Backend plugin installed"
else
  echo "[FAIL] Backend plugin missing ($HERMES_HOME/plugins/meta-harness)"
  fail=$((fail+1))
fi

# Backend plugin.yaml
if [[ -f "$HERMES_HOME/plugins/meta-harness/plugin.yaml" ]]; then
  echo "[PASS] plugin.yaml present"
else
  echo "[FAIL] plugin.yaml missing"
  fail=$((fail+1))
fi

# Backend __init__.py
if [[ -f "$HERMES_HOME/plugins/meta-harness/__init__.py" ]]; then
  echo "[PASS] __init__.py present"
else
  echo "[FAIL] __init__.py missing"
  fail=$((fail+1))
fi

# Backend dashboard manifest
if [[ -f "$HERMES_HOME/plugins/meta-harness/dashboard/manifest.json" ]]; then
  echo "[PASS] dashboard/manifest.json present"
else
  echo "[FAIL] dashboard/manifest.json missing"
  fail=$((fail+1))
fi

# Backend plugin_api
if [[ -f "$HERMES_HOME/plugins/meta-harness/dashboard/plugin_api.py" ]]; then
  echo "[PASS] dashboard/plugin_api.py present"
else
  echo "[FAIL] dashboard/plugin_api.py missing"
  fail=$((fail+1))
fi

# Desktop plugin present
if [[ -f "$HERMES_HOME/desktop-plugins/meta-harness/plugin.js" ]]; then
  echo "[PASS] Desktop plugin installed"
else
  echo "[FAIL] Desktop plugin missing"
  fail=$((fail+1))
fi

# Desktop plugin load simulation — catches missing SDK exports like
# QueryClient/TabsContent that would make Hermes Desktop fail at load.
repo="$(cd "$(dirname "$0")/.." && pwd)"
py="$(command -v python3 || command -v python || true)"
if [[ -n "$py" && -f "$repo/scripts/check_plugin_load.py" ]]; then
  if (cd "$repo" && "$py" scripts/check_plugin_load.py >/dev/null 2>&1); then
    echo "[PASS] Desktop plugin loads (register() ran)"
  else
    echo "[FAIL] Desktop plugin fails to load — see 'python scripts/check_plugin_load.py'"
    fail=$((fail+1))
  fi
fi

# Render test — actually mounts the CommandCenter under React+jsdom.
# Catches runtime errors that load-time inspection misses: undefined
# imports used inside JSX, missing React keys, broken state machines.
if [[ -n "$py" && -f "$repo/scripts/check_plugin_render.py" ]]; then
  if (cd "$repo" && timeout 60 "$py" scripts/check_plugin_render.py >/dev/null 2>&1); then
    echo "[PASS] Desktop plugin renders (CommandCenter mounts without exceptions)"
  else
    echo "[WARN] Desktop render test failed — see 'python scripts/check_plugin_render.py'"
    warn=$((warn+1))
  fi
fi

# Config patch
if [[ -f "$HERMES_HOME/config.yaml" ]]; then
  if grep -q "meta-harness" "$HERMES_HOME/config.yaml"; then
    echo "[PASS] config.yaml references meta-harness"
  else
    echo "[WARN] config.yaml does not reference meta-harness — backend won't auto-load"
    warn=$((warn+1))
  fi
fi

# Pi availability
if command -v pi >/dev/null 2>&1; then
  echo "[PASS] Pi on PATH: $(pi --version 2>&1 | head -n1)"
else
  echo "[WARN] Pi not on PATH — engine.pi will be reported unavailable (degrades gracefully)"
  warn=$((warn+1))
fi

# Python import smoke test — cd to repo so sys.path.insert('hermes-plugin') lands correctly.
repo="$(cd "$(dirname "$0")/.." && pwd)"
py="$(command -v python3 || command -v python || true)"
if [[ -n "$py" ]]; then
  if (cd "$repo" && "$py" - <<'PY' >/dev/null 2>&1
import sys
sys.path.insert(0, 'hermes-plugin')
from hermes_plugin import paths, redaction, capabilities, characters, plugin_lab
from hermes_plugin import topology, engines, store, runtime, api
PY
  ); then
    echo "[PASS] Python modules import cleanly"
  else
    echo "[WARN] Python module import failed — check the hermes_plugin package path"
    warn=$((warn+1))
  fi
else
  echo "[WARN] Python not on PATH — skipping import smoke test"
  warn=$((warn+1))
fi

# Data dir writable
if [[ -d "$HERMES_HOME/meta-harness" ]]; then
  if [[ -w "$HERMES_HOME/meta-harness" ]]; then
    echo "[PASS] data dir writable"
  else
    echo "[FAIL] data dir not writable"
    fail=$((fail+1))
  fi
else
  echo "[INFO] data dir not yet created (will be created on first run)"
fi

echo
if [[ $fail -gt 0 ]]; then
  echo "[RESULT] FAIL ($fail failures, $warn warnings)"
  exit 1
fi
if [[ $warn -gt 0 ]]; then
  echo "[RESULT] PASS with $warn warnings"
  exit 0
fi
echo "[RESULT] PASS"