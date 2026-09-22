#!/usr/bin/env bash
# Meta-Harness uninstaller. Removes the plugin folders and removes the
# meta-harness block from config.yaml (other keys untouched).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -n "${HERMES_HOME:-}" ]]; then
  HERMES_HOME="${HERMES_HOME%/}"
else
  HERMES_HOME="$HOME/.hermes"
fi

if [[ ! -d "$HERMES_HOME" ]]; then
  echo "Hermes Home not found at $HERMES_HOME — nothing to uninstall." >&2
  exit 0
fi

BACKUP_DIR="$HERMES_HOME/backups"
mkdir -p "$BACKUP_DIR"
CONFIG="$HERMES_HOME/config.yaml"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -f "$CONFIG" ]]; then
  cp -p "$CONFIG" "$BACKUP_DIR/config.yaml.before-meta-harness-uninstall.$TS"
fi

rm -rf "$HERMES_HOME/plugins/meta-harness" || true
rm -rf "$HERMES_HOME/desktop-plugins/meta-harness" || true

# Strip the meta-harness block from config.yaml. We only remove lines that
# match the entries.meta-harness tree. Other entries are preserved.
if [[ -f "$CONFIG" ]]; then
  config_arg="$CONFIG"
  if command -v cygpath >/dev/null 2>&1; then
    config_arg="$(cygpath -w "$CONFIG")"
  fi
  python3 - "$config_arg" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
out = []
skip = 0
for line in lines:
    if skip > 0:
        skip -= 1
        continue
    if re.match(r"^\s*meta-harness:\s*$", line):
        # Skip this entry and any continuation indentation.
        # We detect depth relative to this line.
        indent = len(line) - len(line.lstrip())
        skip += 1
        while skip < len(lines):
            nxt = lines[skip]
            stripped = nxt.lstrip()
            cur_indent = len(nxt) - len(stripped)
            if stripped and cur_indent <= indent:
                break
            skip += 1
        skip -= 1
        continue
    out.append(line)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
fi

echo "[ok] Meta-Harness removed."
echo "Backup of your previous config: $BACKUP_DIR/config.yaml.before-meta-harness-uninstall.$TS"