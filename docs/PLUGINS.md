# Plugin model

Two install surfaces, one runtime model.

## Internal plugins (within the harness)

The Plugin Lab manages versioned, model-authored plugins under
`<hermes-home>/meta-harness/generated/<id>/v<N>/`. These never reach the host
plugin loader automatically. Promotion (out of MVP scope) would route
through a human-review step.

## External host plugins (the harness itself)

The harness installs as two host plugin entries — see
`docs/HERMES_INTEGRATION.md` for the exact contract.

| Folder | Door | Default enable |
|---|---|---|
| `~/.hermes/plugins/meta-harness/` | Python backend | off-by-default, opt-in via `config.yaml` |
| `~/.hermes/desktop-plugins/meta-harness/` | Desktop JS | on (standalone door) |

The two doors are independent. Disabling one does not break the other. The
desktop plugin runs entirely without the backend being loaded; it shows a
degraded state when `/api/plugins/meta-harness/status` returns an error.

## Trust levels (model-authored plugins)

| Level | Created by | Can touch host? | Visible in inventory? |
|---|---|---|---|
| `system` | built-in | yes | yes |
| `trusted-installed` | user-installed (this harness) | yes | yes |
| `user-authored` | user Python code | yes (host decides) | yes |
| `model-generated` | Plugin Lab `create` | quarantined | yes |
| `experimental` | Plugin Lab `activate` | never touches host | yes |
| `quarantined` | default for new versions | never touches host | yes |

The four lower levels are visible but inert until a human reviews the
manifest, capability claims, and code; promotion is a separate, gated
action.

## What a plugin manifest must declare

```yaml
id: validation.test-runner
version: 0.1.0
kind: validator

provides:                  # capabilities this plugin implements
  - validation.command
  - validation.test

requires:                  # capabilities this plugin needs at runtime
  - terminal.execute

optional:                  # capabilities used opportunistically
  - git.status

permissions:               # requested trust scope
  - subprocess
  - workspace-read

events:
  emits:    [validation.started, validation.completed]
  consumes: [build.completed]

config_schema:
  ...
```

Not every plugin needs executable code. A character pack, a topology, or a
visual preset are all declarative plugins.

## Manifest categories

```
engine             # an isolated worker runtime adapter
capability         # binds a capability id to a handler
topology           # declarative orchestration graph
role               # prompt pack + allowed capabilities
validator          # a validation gate
renderer           # a UI renderer strategy
character-pack     # declarative sprite + animation manifest
event-sink         # consumes events into an external store
memory             # own memory backend adapter
router             # decides which topology to use for a task
artifact-renderer  # decides how to display an artifact
integration        # bridges to a third-party tool
sandbox            # isolation strategy
```

A plugin can be a single file or a directory; either way, the same manifest
contract applies.

## Lifecycle (host-loaded)

```
DISCOVERED  -> loaded by host plugin loader
ENABLED     -> register(ctx) ran successfully
DISABLED    -> enabled=false in config.yaml, or runtime disable
INVALID     -> manifest failed validation; never enabled
QUARANTINED -> manifest ok, but trust level requires operator action
```

## Plugin Lab lifecycle (model-authored)

```
IDEA
 ↓
CREATE VERSION  -> v0001 written; manifest + code persisted; trust=quarantined
 ↓
STATIC VALIDATION  -> syntax + schema; fail = INVALID, no activation
 ↓
PERMISSION ANALYSIS  -> checked against requested permissions
 ↓
EXPERIMENTAL ACTIVATE  -> pointer updated; trust=experimental; never loaded by host
 ↓
HEALTH CHECK  -> smoke test the experimental plugin's capability claims
 ↓
PROMOTE  (out of MVP scope)
```

No version is destroyed. `rollback` rewinds the pointer.