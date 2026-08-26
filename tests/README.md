# Meta-Harness tests

Unit tests run with no external services:

```bash
python tests/run_all.py
```

or:

```bash
python -m pytest tests/
```

The tests cover:

- redaction (no credentials leak through the event store)
- store (SQLite round-trip for runs / agents / events / character assignments)
- capabilities (registration, trust-based resolution, fallback)
- character packs (animation fallback chain, state mapping completeness)
- plugin lab (create / validate / activate / rollback)
- topology (built-in load, safe cycle handling)
- engines (graceful degradation when host shim / Pi binary is absent)
- redaction bounds (huge strings do not crash)