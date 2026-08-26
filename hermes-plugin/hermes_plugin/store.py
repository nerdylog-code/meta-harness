"""SQLite + JSONL hybrid event / artifact store.

Why SQLite + JSONL:

  * SQLite (WAL): indexed run/agent/task/artifact records. Cheap lookups,
    transactional inserts, no schema drama.
  * JSONL: detailed normalized events for replay / export. Append-only.

The two are intentionally split so a SQLite row references a JSONL offset
instead of storing huge blobs inline.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from .paths import events_path, store_path

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        topology TEXT NOT NULL,
        engine TEXT NOT NULL,
        model TEXT,
        task TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        context TEXT,
        result TEXT,
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        role TEXT NOT NULL,
        engine TEXT NOT NULL,
        model TEXT,
        character TEXT,
        state TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        agent_id TEXT,
        type TEXT NOT NULL,
        path TEXT,
        mime TEXT,
        metadata TEXT,
        created_at REAL NOT NULL,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS plugin_state (
        id TEXT NOT NULL,
        version TEXT NOT NULL,
        kind TEXT NOT NULL,
        trust TEXT NOT NULL,
        state TEXT NOT NULL,
        capabilities TEXT,
        metadata TEXT,
        updated_at REAL NOT NULL,
        PRIMARY KEY(id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS topology_state (
        id TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        state TEXT NOT NULL,
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS character_assignments (
        agent_id TEXT PRIMARY KEY,
        pack TEXT NOT NULL,
        character TEXT NOT NULL,
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS event_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        agent_id TEXT,
        kind TEXT NOT NULL,
        ts REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS event_index_run ON event_index(run_id, id)",
    "CREATE INDEX IF NOT EXISTS event_index_kind ON event_index(kind)",
    "CREATE INDEX IF NOT EXISTS runs_status ON runs(status, updated_at)",
    "CREATE INDEX IF NOT EXISTS agents_run ON agents(run_id)",
]

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_PATH: Path | None = None


def init(db_path: Path | None = None) -> Path:
    """Open the SQLite store. Idempotent."""
    global _CONN, _PATH
    db_path = db_path or store_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if _CONN is not None and _PATH == db_path:
            return db_path
        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use explicit transactions
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        for stmt in _SCHEMA:
            conn.execute(stmt)
        _CONN = conn
        _PATH = db_path
    return db_path


def conn() -> sqlite3.Connection:
    if _CONN is None:
        init()
    assert _CONN is not None
    return _CONN


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def create_run(*, topology: str, engine: str, model: str | None, task: str,
               context: dict | None = None) -> str:
    rid = _new_id("run")
    now = _now()
    with _LOCK:
        c = conn()
        c.execute(
            "INSERT INTO runs(id, topology, engine, model, task, status, "
            "created_at, updated_at, context) VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, topology, engine, model, task, "pending", now, now,
             json.dumps(context or {}, separators=(",", ":"))),
        )
    return rid


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    # JSON-serialise dict/list values; everything else passes through.
    serialised: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            serialised[k] = json.dumps(v, separators=(",", ":"), default=str)
        else:
            serialised[k] = v
    cols = ", ".join(f"{k}=?" for k in serialised)
    args = [serialised[k] for k in serialised]
    args.append(run_id)
    with _LOCK:
        conn().execute(f"UPDATE runs SET {cols} WHERE id=?", args)


def get_run(run_id: str) -> dict | None:
    row = conn().execute(
        "SELECT id, topology, engine, model, task, status, created_at, "
        "updated_at, context, result, error FROM runs WHERE id=?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "topology": row[1], "engine": row[2], "model": row[3],
        "task": row[4], "status": row[5], "created_at": row[6],
        "updated_at": row[7], "context": json.loads(row[8] or "{}"),
        "result": row[9], "error": row[10],
    }


def list_runs(limit: int = 50, status: str | None = None) -> list[dict]:
    if status:
        rows = conn().execute(
            "SELECT id, topology, engine, status, created_at, updated_at, task "
            "FROM runs WHERE status=? ORDER BY updated_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT id, topology, engine, status, created_at, updated_at, task "
            "FROM runs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "topology": r[1], "engine": r[2], "status": r[3],
         "created_at": r[4], "updated_at": r[5], "task": r[6]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def upsert_agent(*, agent_id: str, run_id: str, role: str, engine: str,
                 model: str | None, character: str | None,
                 state: str) -> None:
    now = _now()
    with _LOCK:
        c = conn()
        c.execute(
            "INSERT INTO agents(id, run_id, role, engine, model, character, "
            "state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "state=excluded.state, character=excluded.character, updated_at=excluded.updated_at",
            (agent_id, run_id, role, engine, model, character, state, now, now),
        )


def update_agent(agent_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    args = [fields[k] for k in fields]
    args.append(agent_id)
    with _LOCK:
        conn().execute(f"UPDATE agents SET {cols} WHERE id=?", args)


def list_agents(run_id: str) -> list[dict]:
    rows = conn().execute(
        "SELECT id, run_id, role, engine, model, character, state, "
        "created_at, updated_at FROM agents WHERE run_id=? "
        "ORDER BY created_at",
        (run_id,),
    ).fetchall()
    return [
        {"id": r[0], "run_id": r[1], "role": r[2], "engine": r[3],
         "model": r[4], "character": r[5], "state": r[6],
         "created_at": r[7], "updated_at": r[8]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def create_artifact(*, run_id: str, agent_id: str | None, type_: str,
                    path: str | None, mime: str | None,
                    metadata: dict | None = None) -> str:
    aid = _new_id("art")
    now = _now()
    with _LOCK:
        conn().execute(
            "INSERT INTO artifacts(id, run_id, agent_id, type, path, mime, "
            "metadata, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (aid, run_id, agent_id, type_, path, mime,
             json.dumps(metadata or {}, separators=(",", ":")), now),
        )
    return aid


def get_artifact(artifact_id: str) -> dict | None:
    row = conn().execute(
        "SELECT id, run_id, agent_id, type, path, mime, metadata, created_at "
        "FROM artifacts WHERE id=?",
        (artifact_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "run_id": row[1], "agent_id": row[2], "type": row[3],
        "path": row[4], "mime": row[5],
        "metadata": json.loads(row[6] or "{}"), "created_at": row[7],
    }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def append_event(event: dict) -> None:
    """Append one normalized event to JSONL + index row in SQLite."""
    event = dict(event)
    event.setdefault("ts", _now())
    event.setdefault("seq", _new_id("ev"))
    line = json.dumps(event, separators=(",", ":"), default=str)
    with _LOCK:
        c = conn()
        c.execute("BEGIN")
        try:
            with open(events_path(), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            c.execute(
                "INSERT INTO event_index(run_id, agent_id, kind, ts) VALUES(?,?,?,?)",
                (event.get("run_id"), event.get("agent_id"),
                 event.get("event", "unknown"), float(event["ts"])),
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def list_events(run_id: str, since_id: int = 0, limit: int = 200) -> list[dict]:
    """Return recent events for a run. Bounded; raw events live in JSONL."""
    rows = conn().execute(
        "SELECT id, run_id, agent_id, kind, ts FROM event_index "
        "WHERE run_id=? AND id > ? ORDER BY id ASC LIMIT ?",
        (run_id, since_id, limit),
    ).fetchall()
    return [
        {"id": r[0], "run_id": r[1], "agent_id": r[2],
         "kind": r[3], "ts": r[4]}
        for r in rows
    ]


def tail_events(run_id: str, since_id: int = 0) -> Iterator[dict]:
    """Stream events from JSONL newer than since_id."""
    # We never read the entire JSONL into memory. The index is used for
    # existence; the JSONL is the source of truth for content.
    path = events_path()
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("run_id") != run_id:
                continue
            yield evt


# ---------------------------------------------------------------------------
# Plugin state + topology state + character assignments
# ---------------------------------------------------------------------------


def upsert_plugin_state(plugin_id: str, version: str, kind: str, trust: str,
                        state: str, capabilities: list[str] | None,
                        metadata: dict | None) -> None:
    now = _now()
    with _LOCK:
        conn().execute(
            "INSERT INTO plugin_state(id, version, kind, trust, state, "
            "capabilities, metadata, updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id, version) DO UPDATE SET "
            "kind=excluded.kind, trust=excluded.trust, state=excluded.state, "
            "capabilities=excluded.capabilities, metadata=excluded.metadata, "
            "updated_at=excluded.updated_at",
            (plugin_id, version, kind, trust, state,
             json.dumps(capabilities or []),
             json.dumps(metadata or {}, separators=(",", ":")), now),
        )


def list_plugins() -> list[dict]:
    rows = conn().execute(
        "SELECT id, version, kind, trust, state, capabilities, metadata, updated_at "
        "FROM plugin_state ORDER BY updated_at DESC",
    ).fetchall()
    return [
        {"id": r[0], "version": r[1], "kind": r[2], "trust": r[3],
         "state": r[4], "capabilities": json.loads(r[5] or "[]"),
         "metadata": json.loads(r[6] or "{}"), "updated_at": r[7]}
        for r in rows
    ]


def get_plugin(plugin_id: str) -> dict | None:
    rows = conn().execute(
        "SELECT id, version, kind, trust, state, capabilities, metadata, updated_at "
        "FROM plugin_state WHERE id=? ORDER BY updated_at DESC LIMIT 1",
        (plugin_id,),
    ).fetchall()
    if not rows:
        return None
    r = rows[0]
    return {"id": r[0], "version": r[1], "kind": r[2], "trust": r[3],
            "state": r[4], "capabilities": json.loads(r[5] or "[]"),
            "metadata": json.loads(r[6] or "{}"), "updated_at": r[7]}


def set_current_version(plugin_id: str, version: str) -> None:
    """Update the per-plugin current version pointer in metadata."""
    row = conn().execute(
        "SELECT metadata FROM plugin_state WHERE id=? ORDER BY updated_at DESC LIMIT 1",
        (plugin_id,),
    ).fetchone()
    if not row:
        return
    md = json.loads(row[0] or "{}")
    md["current"] = version
    conn().execute(
        "UPDATE plugin_state SET metadata=?, updated_at=? WHERE id=?",
        (json.dumps(md, separators=(",", ":")), _now(), plugin_id),
    )


def upsert_topology_state(topology_id: str, version: str, state: str) -> None:
    with _LOCK:
        conn().execute(
            "INSERT INTO topology_state(id, version, state, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "version=excluded.version, state=excluded.state, updated_at=excluded.updated_at",
            (topology_id, version, state, _now()),
        )


def get_topology_state(topology_id: str) -> dict | None:
    row = conn().execute(
        "SELECT id, version, state, updated_at FROM topology_state WHERE id=?",
        (topology_id,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "version": row[1], "state": row[2],
            "updated_at": row[3]}


def assign_character(agent_id: str, pack: str, character: str) -> None:
    with _LOCK:
        conn().execute(
            "INSERT INTO character_assignments(agent_id, pack, character, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "pack=excluded.pack, character=excluded.character, updated_at=excluded.updated_at",
            (agent_id, pack, character, _now()),
        )


def get_character_assignment(agent_id: str) -> dict | None:
    row = conn().execute(
        "SELECT agent_id, pack, character, updated_at FROM character_assignments "
        "WHERE agent_id=?",
        (agent_id,),
    ).fetchone()
    if not row:
        return None
    return {"agent_id": row[0], "pack": row[1], "character": row[2],
            "updated_at": row[3]}


def list_assignments() -> list[dict]:
    rows = conn().execute(
        "SELECT agent_id, pack, character, updated_at FROM character_assignments",
    ).fetchall()
    return [
        {"agent_id": r[0], "pack": r[1], "character": r[2], "updated_at": r[3]}
        for r in rows
    ]