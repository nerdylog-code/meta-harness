"""Hermes-plugin runtime package — installed next to __init__.py.

Modules:

  * ``runtime``   — bootstrap, hook adapters, model-facing tools.
  * ``api``       — FastAPI router mounted at /api/plugins/meta-harness/.
  * ``store``     — SQLite + JSONL hybrid event/artifact store.
  * ``capabilities`` — internal capability registry + resolver.
  * ``topology``  — declarative topology executor.
  * ``engines``   — engine.hermes + engine.pi adapters.
  * ``validation`` — gate runtime with retry caps.
  * ``characters`` — character pack registry + animation mapping.
  * ``plugin_lab`` — versioned model-authored plugin experiments.
  * ``redaction``  — credential / secret scrubbing.
  * ``paths``      — hermes_home / data_dir / generated paths.

The package is deliberately small. Each module exposes a tiny API; the rest
of the plugin talks through ``runtime``.
"""