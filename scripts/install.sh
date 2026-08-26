#!/usr/bin/env bash
# Meta-Harness installer.
#
# Idempotent. Creates a timestamped backup of ~/.hermes/config.yaml before
# patching it. Copies the backend plugin and desktop plugin into place.
# NEVER overwrites user files; only patches the required keys.
#
# Usage: bash scripts/install.sh [hermes-home]
#        (default: $HERMES_HOME or ~/.hermes)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $# -ge 1 ]]; then
  HERMES_HOME="${1%/}"
elif [[ -n "${HERMES_HOME:-}" ]]; then
  HERMES_HOME="${HERMES_HOME%/}"
else
  HERMES_HOME="$HOME/.hermes"
fi

if [[ ! -d "$HERMES_HOME" ]]; then
  echo "ERROR: Hermes Home not found at $HERMES_HOME" >&2
  echo "Set HERMES_HOME or pass it as the first argument." >&2
  exit 1
fi

BACKUP_DIR="$HERMES_HOME/backups"
mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
CONFIG="$HERMES_HOME/config.yaml"
if [[ -f "$CONFIG" ]]; then
  cp -p "$CONFIG" "$BACKUP_DIR/config.yaml.before-meta-harness.$TS"
  echo "[ok] backed up config -> $BACKUP_DIR/config.yaml.before-meta-harness.$TS"
fi

mkdir -p "$HERMES_HOME/plugins/meta-harness"
mkdir -p "$HERMES_HOME/desktop-plugins/meta-harness"
mkdir -p "$HERMES_HOME/meta-harness"

# Backend plugin
cp -r "$ROOT/hermes-plugin/." "$HERMES_HOME/plugins/meta-harness/"
echo "[ok] backend plugin -> $HERMES_HOME/plugins/meta-harness"

# Desktop plugin (only the JS — the backend uses its own copy).
cp "$ROOT/desktop-plugin/plugin.js" "$HERMES_HOME/desktop-plugins/meta-harness/plugin.js"
echo "[ok] desktop plugin -> $HERMES_HOME/desktop-plugins/meta-harness"

# Patch config.yaml — only the keys we need.
if [[ -f "$CONFIG" ]]; then
  if grep -q "^plugins:" "$CONFIG"; then
    if ! grep -q "meta-harness" "$CONFIG"; then
      python3 - "$CONFIG" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
if "meta-harness" in text:
    sys.exit(0)

# Insert into an existing plugins.entries block when one already
# exists; otherwise create a new plugins: section. Idempotent.
if re.search(r"^\s*entries:\s*$", text, flags=re.M):
    # Find the entries: line and append our entry after it (preserving
    # the indentation of the existing entries).
    text = re.sub(
        r"(^\s*entries:\s*\n)",
        r"\1    meta-harness:\n      enabled: true\n      config: {}\n",
        text, count=1, flags=re.M
    )
elif re.search(r"^plugins:\s*$", text, flags=re.M):
    text = re.sub(
        r"(^plugins:\s*$)",
        r"\1\n  entries:\n    meta-harness:\n      enabled: true\n      config: {}\n",
        text, count=1, flags=re.M
    )
else:
    text += "\nplugins:\n  entries:\n    meta-harness:\n      enabled: true\n      config: {}\n"

p.write_text(text, encoding="utf-8")
PY
      echo "[ok] config.yaml patched (plugins.entries.meta-harness)"
    else
      echo "[ok] config.yaml already references meta-harness"
    fi
  else
    printf '\nplugins:\n  entries:\n    meta-harness:\n      enabled: true\n      config: {}\n' >> "$CONFIG"
    echo "[ok] config.yaml created plugins.entries.meta-harness"
  fi
fi

cat <<EOF

[done] Meta-Harness installed.

  backend plugin : $HERMES_HOME/plugins/meta-harness
  desktop plugin : $HERMES_HOME/desktop-plugins/meta-harness
  data dir       : $HERMES_HOME/meta-harness

Next:
  1. Restart Hermes (or run 'hermes dashboard' / open Hermes Desktop).
  2. Open Hermes Desktop -> Sidebar -> Meta-Harness.
  3. ⌘K -> "Reload desktop plugins" if the sidebar row is missing.

Uninstall:
  bash $ROOT/scripts/uninstall.sh

Doctor:
  bash $ROOT/scripts/doctor.sh
EOF