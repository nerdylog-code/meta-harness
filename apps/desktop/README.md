# Meta-Harness desktop shell (WP-007)

A Tauri v2 shell that starts the daemon and loads the same web UI served by
`apps/web`. The shell is **optional by design**: `python scripts/dev.py` and a
browser must keep working without it (PROJECT_BOOK §4/§6).

Not started: WP-007 depends on WP-006 (the web app it loads).
