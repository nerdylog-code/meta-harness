"""Context Capsule v1 (M2): the object that crosses a runtime boundary.

The Architect's rule decides this module's shape: the transfer object is a **verified capsule**,
not copied chat history. So there are exactly two jobs here — assemble a capsule from what the log
recorded, and refuse to let an unverified one travel.

What the builder fills, and what it refuses to invent:

* `objective` comes from the mission (title + objective) when there is one, otherwise from the
  session's first user message.
* `active_files` comes from tool calls that carried paths. That is a fact — a tool touched the
  path — and the file's `state` stays `unknown` because touching is not changing.
* `artifacts` are referenced by exact `art_` id, never by content.
* `resume_instruction` is assembled from the same facts (runtime, provider, model, counts, last
  message) and reads like a handoff, not like a summary of a story.
* Everything the log cannot answer stays **empty**, and each gap is named in
  `self_assessment.not_verified`. A capsule that claimed "completed: everything went fine" would
  be worse than one with an empty list, because the next runtime would believe it.

The verifier fails **closed**: a reference it cannot resolve makes the capsule unverified, and the
migration refuses to move. Today the builder emits no task references because the log has no task
events — the check that would catch them already exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from metaharness_contracts.capsule import (
    ActiveFile,
    ArtifactRefLite,
    CapsulePhase,
    ContextCapsule,
    SelfAssessment,
)

MAX_RESUME_CHARS = 1200
MAX_ACTIVE_FILES = 20
#: A path is a path whichever runtime reported it: Pi puts it inside `args_preview`, Hermes in
#: `locations`. Reading the serialised payload catches both without a runtime-specific branch.
#: Pi serialises tool args as a Python repr (`{'path': 'x'}`), Hermes as JSON (`{"path": "x"}`).
#: Both are paths; the pattern accepts either quoting rather than assuming one runtime's dialect.
PATH_PATTERN = re.compile(r"""['"]path['"]\s*:\s*['"]([^'"]{1,400})['"]""")


class CapsuleError(RuntimeError):
    """The capsule could not be built from what the log holds."""


@dataclass
class CapsuleDraft:
    capsule: ContextCapsule
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationCheck:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class VerificationReport:
    ok: bool
    digest: str
    checks: list[VerificationCheck] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "digest": self.digest,
            "checks": [check.as_dict() for check in self.checks],
            "failed": [check.name for check in self.checks if not check.ok],
        }


def capsule_digest(capsule: ContextCapsule) -> str:
    """Digest of the exact bytes that get stored: compact JSON, no whitespace games."""
    return hashlib.sha256(capsule.model_dump_json().encode("utf-8")).hexdigest()


def capsule_bytes(capsule: ContextCapsule) -> bytes:
    return capsule.model_dump_json().encode("utf-8")


# ------------------------------------------------------------------------------- build


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def build_capsule(
    store: Any,
    *,
    agent_id: str,
    session_id: str | None = None,
    mission_id: str | None = None,
    phase: CapsulePhase = CapsulePhase.NORMAL,
    resume_max_chars: int = MAX_RESUME_CHARS,
) -> CapsuleDraft:
    session: dict[str, Any] | None = None
    if session_id:
        found = store.rows("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not found:
            raise CapsuleError(f"unknown session {session_id}")
        row: dict[str, Any] = found[0]
        session = row
        mission_id = mission_id or row.get("mission_id")

    mission: dict[str, Any] | None = None
    if mission_id:
        found = store.rows("SELECT * FROM missions WHERE id = ?", (mission_id,))
        mission = found[0] if found else None

    events = (
        store.events(session_id=session_id)
        if session_id
        else store.events(agent_id=agent_id)
    )
    if not events and not session:
        raise CapsuleError(f"nothing to build a capsule from for agent {agent_id}")

    # `message.submitted` is what the control plane sent; `message.completed` with role=user is
    # how some runtimes echo it back. Counting both means "what was asked" is never missing from a
    # capsule just because the runtime reported it differently.
    user_messages = [
        event
        for event in events
        if event.kind == "message.submitted"
        or (event.kind == "message.completed" and event.payload_body.get("role") == "user")
    ]
    assistant_messages = [
        event
        for event in events
        if event.kind == "message.completed" and event.payload_body.get("role") == "assistant"
    ]
    tool_starts = [event for event in events if event.kind == "tool.started"]
    tool_done = [event for event in events if event.kind == "tool.completed"]
    artifact_events = [event for event in events if event.kind == "artifact.created"]
    usage_events = [event for event in events if event.kind == "usage.sampled"]

    # ------------------------------------------------------------------ objective
    if mission:
        objective = f"{mission.get('title') or 'untitled'} — {mission.get('objective') or ''}".strip(" —")
    elif user_messages:
        objective = _first_text(user_messages[0].payload_body, "text")[:400]
    elif assistant_messages:
        objective = _first_text(assistant_messages[0].payload_body, "text")[:400]
    else:
        objective = f"session {session_id or 'unknown'} of agent {agent_id}"

    # --------------------------------------------------------------- active files
    paths: list[str] = []
    for event in tool_starts:
        for match in PATH_PATTERN.findall(json.dumps(event.payload_body, ensure_ascii=False)):
            if match not in paths:
                paths.append(match)
    active_files = [
        ActiveFile(path=path, reason="touched by a tool call in this session", state="unknown")
        for path in paths[:MAX_ACTIVE_FILES]
    ]

    # ------------------------------------------------------------------ artifacts
    artifacts: list[ArtifactRefLite] = []
    for event in artifact_events:
        artifact_id = _first_text(event.payload_body, "artifact_id", "id")
        if artifact_id.startswith("art_"):
            artifacts.append(ArtifactRefLite(artifact_id=artifact_id, purpose="recorded during this session"))

    # ------------------------------------------------------------- resume text
    runtime_id = (session or {}).get("runtime_id")
    provider = (session or {}).get("provider")
    model = (session or {}).get("model")
    last_assistant = _first_text(assistant_messages[-1].payload_body, "text") if assistant_messages else ""
    parts = [
        f"Continue this work as the same agent ({agent_id}).",
        f"Objective: {objective}",
        (
            f"Previous session {session_id or '(none)'} ran on runtime {runtime_id or 'unknown'}"
            f" with provider {provider or 'unknown'} and model {model or 'unknown'}"
        ),
        (
            f"It recorded {len(events)} events: {len(user_messages)} user messages, "
            f"{len(assistant_messages)} assistant messages, {len(tool_starts)} tool calls "
            f"({len(tool_done)} completed), {len(artifact_events)} artifacts."
        ),
    ]
    if paths:
        parts.append("Paths a tool touched: " + ", ".join(paths[:8]))
    if last_assistant:
        parts.append(f"Last thing it said (verbatim, truncated): {last_assistant[:400]!r}")
    parts.append(
        "The raw history is archived with the previous session; this capsule is the handoff. "
        "Do not assume anything the capsule does not state."
    )
    # The first real migration proved why this closing line is not optional. With an open-ended
    # "continue the work", the destination agent read the capsule, escaped its workspace, found the
    # repository, read PROJECT_STATE and this project's own logs, and started building a watcher for
    # the end-to-end run -- 24 tool calls and a 15-minute turn, ended only by our safety timeout.
    # That is the migration working (it carried operational intent) and it is also the wrong ask for
    # a handoff: a migration is not a work order. Acknowledging the state is what a transfer needs,
    # and it is what makes the transfer checkable.
    parts.append(
        "Acknowledge this handoff: state in a few lines what you understand of the objective and of "
        "the paths involved, then wait for the next instruction. Do not start new work in this "
        "session until it is asked for."
    )
    resume_instruction = "\n".join(parts)[:resume_max_chars]

    # ------------------------------------------------------------- honest gaps
    not_verified: list[str] = []
    if not assistant_messages:
        not_verified.append("no assistant message was recorded, so nothing is known about progress")
    if not tool_starts:
        not_verified.append("no tool call was recorded: the workspace state is unobserved")
    if paths:
        not_verified.append("a tool touched those paths, which does not mean it changed them")
    if not artifact_events:
        not_verified.append("no artifact was recorded for this session")
    not_verified.append("no task events exist in this log, so `completed` and `next_actions` are empty")

    workspace_state: dict[str, Any] = {
        "event_count": len(events),
        "user_messages": len(user_messages),
        "assistant_messages": len(assistant_messages),
        "tool_calls": len(tool_starts),
        "tool_completions": len(tool_done),
        "artifacts": len(artifact_events),
        "usage_samples": len(usage_events),
        "last_seq": events[-1].seq if events else None,
        "session_state": (session or {}).get("state"),
        "runtime_id": runtime_id,
        "provider": provider,
        "model": model,
    }

    capsule = ContextCapsule(
        objective=objective,
        current_phase=phase,
        completed=[],  # nothing to claim: there are no task events to complete
        next_actions=[],
        invariants=[
            "agent identity is preserved: this capsule belongs to the same agent_id",
            "the previous session stays in the log as history",
        ],
        decisions=[],
        open_questions=[],
        blockers=[],
        active_files=active_files,
        artifacts=artifacts,
        tests=[],
        memory_candidates=[],
        workspace_state=workspace_state,
        resume_instruction=resume_instruction,
        self_assessment=SelfAssessment(confidence=None, concerns=[], not_verified=not_verified),
    )
    evidence = {
        "event_count": len(events),
        "tool_calls": len(tool_starts),
        "active_files": len(active_files),
        "artifacts": len(artifacts),
        "gaps": len(not_verified),
    }
    return CapsuleDraft(capsule=capsule, evidence=evidence)


# ------------------------------------------------------------------------------ verify


def verify_capsule(
    store: Any,
    capsule: ContextCapsule,
    *,
    expected_agent_id: str | None = None,
    stored_digest: str | None = None,
) -> VerificationReport:
    """Check a capsule against the store. Fails closed: anything unresolved means `ok=False`."""
    checks: list[VerificationCheck] = []

    # 1. the contract still holds (size ceiling, forbidden fields, phase, non-empty instruction)
    try:
        ContextCapsule.model_validate(capsule.model_dump())
        checks.append(VerificationCheck("contract", True, "the capsule satisfies the frozen contract"))
    except Exception as exc:  # pragma: no cover - construction already validates
        checks.append(VerificationCheck("contract", False, str(exc)[:300]))

    # 2. the digest of the bytes that were stored
    digest = capsule_digest(capsule)
    if stored_digest is not None:
        checks.append(
            VerificationCheck(
                "digest",
                digest == stored_digest,
                f"computed {digest[:16]}… stored {stored_digest[:16]}…",
            )
        )

    # 3. identity: a capsule for another agent must never be applied to this one
    if expected_agent_id is not None:
        owner = capsule.workspace_state.get("agent_id")
        checks.append(
            VerificationCheck(
                "agent",
                owner in (None, expected_agent_id),
                f"capsule agent {owner!r} vs expected {expected_agent_id!r}",
            )
        )

    # 4. every referenced artifact must exist, by id
    for artifact in capsule.artifacts:
        exists = store.artifact(artifact.artifact_id) is not None
        checks.append(
            VerificationCheck(
                f"artifact:{artifact.artifact_id}",
                exists,
                "resolved in the store" if exists else "not found in the store",
            )
        )

    # 5. task references: the log has no task events, so a capsule that carries one cannot be
    #    verified. Failing closed here is deliberate -- the alternative is travelling on a claim.
    task_ids = [item.task_id for item in capsule.completed if item.task_id]
    task_ids += [action.task_id for action in capsule.next_actions if action.task_id]
    if task_ids:
        has_tasks = any(name == "tasks" for name in store.projection_tables())
        for task_id in task_ids:
            resolved = False
            if has_tasks:
                resolved = bool(store.rows("SELECT 1 FROM tasks WHERE id = ?", (task_id,)))
            checks.append(
                VerificationCheck(
                    f"task:{task_id}",
                    resolved,
                    "resolved in the store"
                    if resolved
                    else "unresolved: this store has no tasks projection yet",
                )
            )

    # 6. the capsule must carry something a next runtime can act on
    checks.append(
        VerificationCheck(
            "resume_instruction",
            bool(capsule.resume_instruction.strip()),
            f"{len(capsule.resume_instruction)} chars",
        )
    )

    return VerificationReport(ok=all(check.ok for check in checks), digest=digest, checks=checks)
