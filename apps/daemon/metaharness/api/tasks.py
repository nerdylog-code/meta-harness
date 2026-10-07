"""The work graph API: tasks, dependencies, transitions (Tasks / Work Graph).

The graph is canonical in the daemon: every transition publishes a `task.*` event and the
projection folds it, so a restart rebuilds the same graph and a replay reproduces it exactly. This
module holds the *rules*, and it calls the pure ones from `metaharness_contracts.taskgraph` rather
than restating them -- a cycle is a cycle wherever it is asked about.

The rules that are enforced here, and why each one is a refusal rather than a warning:

* a dependency must name a task this mission has -- a dangling edge cannot support "ready";
* the graph must stay a DAG -- a cycle is not a work plan, it is a deadlock;
* a task starts only when its dependencies are satisfied -- otherwise "ready" means nothing;
* a run may complete only the task it belongs to -- a run is not a universal key;
* a completion carries the evidence its acceptance gate requires -- "the model said done" is not
  a gate (BOOK §3.15/§81), and an agent does not decide that its own work is accepted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from metaharness_contracts import IdKind, new_id
from metaharness_contracts.taskgraph import (
    SATISFIED_STATES,
    STARTABLE_STATES,
    TaskGraphError,
    blocked_ids,
    find_cycle,
    is_ready,
    missing_dependencies,
    ready_ids,
)

router = APIRouter(tags=["tasks"])


# --------------------------------------------------------------------------------- payloads


class TaskIn(BaseModel):
    """What a caller may say when creating a task. One concept, one field: `description`.

    Extras are forbidden rather than ignored: a client that still sends `objective` gets a 422
    naming the field, instead of a task whose description silently stayed empty.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    acceptance_gate: dict[str, Any] | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    requires_artifact: bool = False
    owner_agent: str | None = None
    workspace_scope: str | None = None
    parent_id: str | None = None


class DependencyIn(BaseModel):
    depends_on: str


class AssignIn(BaseModel):
    agent_id: str


class StartIn(BaseModel):
    run_id: str | None = None
    agent_id: str | None = None


class CompleteIn(BaseModel):
    run_id: str | None = None
    proof: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    note: str | None = None


class ReasonIn(BaseModel):
    reason: str | None = None


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    workspace_scope: str | None = None


# --------------------------------------------------------------------------------- reading


def _store(request: Request):
    return request.app.state.store


def _bus(request: Request):
    return request.app.state.bus


def _graph(store, mission_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Tasks and edges, in deterministic order. The one place the API reads the graph from."""
    tasks = {
        row["id"]: row
        for row in store.rows("SELECT * FROM tasks WHERE mission_id = ? ORDER BY created_ts, id", (mission_id,))
    }
    edges: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for row in store.rows(
        "SELECT task_id, depends_on FROM task_dependencies ORDER BY task_id, depends_on"
    ):
        if row["task_id"] in edges:
            edges[row["task_id"]].append(row["depends_on"])
    return tasks, edges


def _states(tasks: dict[str, dict[str, Any]]):
    from metaharness_contracts import TaskState

    return {task_id: TaskState(row["state"]) for task_id, row in tasks.items()}


def _task_or_404(store, task_id: str) -> dict[str, Any]:
    row = store.rows("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"no such task: {task_id}")
    return row[0]


def _view(task: dict[str, Any], dependencies: list[str], states: dict) -> dict[str, Any]:
    from metaharness_contracts import TaskState

    state = TaskState(task["state"])
    return {
        "id": task["id"],
        "mission_id": task["mission_id"],
        "title": task["title"],
        "description": task["description"],
        "state": task["state"],
        "owner_agent": task["owner_agent"],
        "run_id": task["run_id"],
        "workspace_scope": task["workspace_scope"],
        "dependencies": dependencies,
        "dependents": [],
        "ready": is_ready(state, dependencies, states),
        "created_at": task["created_ts"],
        "completed_at": task["completed_ts"],
        "proof": task["proof"],
        "artifacts": task["artifacts"],
        "acceptance": task["acceptance"],
    }


def _publish(request: Request, kind: str, payload: dict[str, Any], *, task: dict[str, Any]) -> None:
    _bus(request).publish(
        kind,
        {"task_id": task["id"], **payload},
        method="measured",
        task_id=task["id"],
        mission_id=task["mission_id"],
        agent_id=task.get("owner_agent"),
        run_id=task.get("run_id"),
    )


def _sync_states(request: Request, mission_id: str) -> list[str]:
    """Move tasks to the state the graph says they are in, and say so in the log.

    A task is `ready` only while every dependency it has is satisfied, so this both releases
    dependents and demotes a task whose readiness a new edge just took away. Publishing nothing
    when nothing changed keeps the log readable; publishing the change is what makes the state a
    fact about the graph rather than a label somebody set.
    """
    from metaharness_contracts import TaskState

    store = _store(request)
    tasks, edges = _graph(store, mission_id)
    states = _states(tasks)
    released: list[str] = []
    for task_id in sorted(tasks):
        row = tasks[task_id]
        state = TaskState(row["state"])
        if state in {TaskState.DONE, TaskState.CANCELLED, TaskState.FAILED, TaskState.RUNNING, TaskState.REVIEW}:
            continue
        deps = edges.get(task_id, [])
        if is_ready(state, deps, states):
            if state is not TaskState.READY:
                _publish(request, "task.ready", {"state": "ready"}, task=row)
                released.append(task_id)
        elif state is TaskState.READY:
            unsatisfied = [dep for dep in deps if states.get(dep) not in SATISFIED_STATES]
            _publish(request, "task.blocked", {"reason": f"waiting on {unsatisfied}"}, task=row)
    return released


def _check_edges(request: Request, mission_id: str, task_id: str, edges: dict[str, list[str]]) -> None:
    """Refuse a dangling edge or a cycle before anything is written."""
    store = _store(request)
    known = set(edges)
    missing = missing_dependencies(task_id, edges.get(task_id, []), known)
    if missing:
        raise HTTPException(
            status_code=409, detail=f"dependencies that do not exist in this mission: {sorted(missing)}"
        )
    cycle = find_cycle({**edges, task_id: edges.get(task_id, [])})
    if cycle:
        raise HTTPException(status_code=409, detail=f"that dependency would create a cycle: {cycle}")


# --------------------------------------------------------------------------------- endpoints


@router.get("/v1/missions/{mission_id}/tasks")
def list_tasks(mission_id: str, request: Request) -> dict[str, Any]:
    """The work graph: every task with its edges, and what is ready right now."""
    store = _store(request)
    tasks, edges = _graph(store, mission_id)
    states = _states(tasks)
    dependents: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for task_id, deps in edges.items():
        for dep in deps:
            if dep in dependents:
                dependents[dep].append(task_id)
    views = []
    for task_id, row in tasks.items():
        view = _view(row, edges.get(task_id, []), states)
        view["dependents"] = sorted(dependents.get(task_id, []))
        views.append(view)
    return {
        "mission_id": mission_id,
        "tasks": views,
        "ready": ready_ids(states, edges),
        "blocked": blocked_ids(states, edges),
        "order": sorted(tasks, key=lambda task_id: (tasks[task_id]["created_ts"], task_id)),
    }


@router.get("/v1/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    _, edges = _graph(store, task["mission_id"])
    states = _states(_graph(store, task["mission_id"])[0])
    return _view(task, edges.get(task_id, []), states)


@router.post("/v1/missions/{mission_id}/tasks")
def create_task(mission_id: str, payload: TaskIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    if not store.rows("SELECT id FROM missions WHERE id = ?", (mission_id,)):
        raise HTTPException(status_code=404, detail=f"no such mission: {mission_id}")
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="a task needs a title")

    tasks, edges = _graph(store, mission_id)
    task_id = new_id(IdKind.TASK)
    gate = payload.acceptance_gate or {
        "criteria": payload.acceptance_criteria,
        "requires_artifact": payload.requires_artifact,
    }
    if not gate.get("criteria") and not gate.get("command") and not gate.get("requires_artifact"):
        # An acceptance gate that accepts anything accepts nothing: the Book's rule is that a task
        # proves it is done, so a gate must at least name what proof means.
        gate = {**gate, "criteria": ["a recorded result for this task"]}

    candidate = dict(edges)
    candidate[task_id] = list(payload.dependencies)
    _check_edges(request, mission_id, task_id, candidate)

    event = store.new_event(
        "task.created",
        {
            "task_id": task_id,
            "mission_id": mission_id,
            "title": payload.title,
            "description": payload.description,
            "state": "draft",
            "owner_agent": payload.owner_agent,
            "workspace_scope": payload.workspace_scope,
            "acceptance_gate": gate,
            "dependencies": sorted(set(payload.dependencies)),
        },
        task_id=task_id,
        mission_id=mission_id,
        agent_id=payload.owner_agent,
    )
    store.append(event)
    _sync_states(request, mission_id)
    tasks, edges = _graph(store, mission_id)
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/dependencies")
def add_dependency(task_id: str, payload: DependencyIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    if payload.depends_on == task_id:
        raise HTTPException(status_code=409, detail="a task cannot depend on itself")
    tasks, edges = _graph(store, task["mission_id"])
    candidate = {**edges, task_id: sorted({*edges.get(task_id, []), payload.depends_on})}
    _check_edges(request, task["mission_id"], task_id, candidate)
    store.append(
        store.new_event(
            "task.dependency_added",
            {"task_id": task_id, "depends_on": payload.depends_on},
            task_id=task_id,
            mission_id=task["mission_id"],
        )
    )
    _sync_states(request, task["mission_id"])
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.delete("/v1/tasks/{task_id}/dependencies/{depends_on}")
def remove_dependency(task_id: str, depends_on: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    store.append(
        store.new_event(
            "task.dependency_removed",
            {"task_id": task_id, "depends_on": depends_on},
            task_id=task_id,
            mission_id=task["mission_id"],
        )
    )
    _sync_states(request, task["mission_id"])
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/assign")
def assign_task(task_id: str, payload: AssignIn, request: Request) -> dict[str, Any]:
    """Assignment is a property of the task, never of a run: identity outlives execution."""
    store = _store(request)
    task = _task_or_404(store, task_id)
    if not store.rows("SELECT id FROM agents WHERE id = ?", (payload.agent_id,)):
        raise HTTPException(status_code=404, detail=f"no such agent: {payload.agent_id}")
    store.append(
        store.new_event(
            "task.assigned",
            {"task_id": task_id, "agent_id": payload.agent_id},
            task_id=task_id,
            mission_id=task["mission_id"],
            agent_id=payload.agent_id,
        )
    )
    updated = _task_or_404(store, task_id)
    tasks, edges = _graph(store, task["mission_id"])
    return _view(updated, edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/start")
def start_task(task_id: str, payload: StartIn, request: Request) -> dict[str, Any]:
    """Start a task. A task whose dependencies are not satisfied does not start -- it is blocked."""
    from metaharness_contracts import TaskState

    store = _store(request)
    task = _task_or_404(store, task_id)
    state = TaskState(task["state"])
    if state not in STARTABLE_STATES:
        raise HTTPException(
            status_code=409, detail=f"task {task_id} is {task['state']} and cannot start from there"
        )
    tasks, edges = _graph(store, task["mission_id"])
    states = _states(tasks)
    deps = edges.get(task_id, [])
    if not is_ready(state, deps, states):
        unsatisfied = [dep for dep in deps if states.get(dep) not in SATISFIED_STATES]
        _publish(request, "task.blocked", {"reason": f"waiting on {unsatisfied}"}, task=task)
        raise HTTPException(
            status_code=409,
            detail=f"task {task_id} is blocked: dependencies not satisfied ({unsatisfied})",
        )
    store.append(
        store.new_event(
            "task.started",
            {"task_id": task_id, "run_id": payload.run_id, "agent_id": payload.agent_id},
            task_id=task_id,
            mission_id=task["mission_id"],
            run_id=payload.run_id,
            agent_id=payload.agent_id or task["owner_agent"],
        )
    )
    updated = _task_or_404(store, task_id)
    return _view(updated, deps, states)


@router.post("/v1/tasks/{task_id}/complete")
def complete_task(task_id: str, payload: CompleteIn, request: Request) -> dict[str, Any]:
    """Complete a task, with the evidence its acceptance gate requires.

    Two refusals matter here. A run that belongs to another task may not complete this one, and a
    gate that requires an artifact is not satisfied by an agent's sentence.
    """
    from metaharness_contracts import TaskState

    store = _store(request)
    task = _task_or_404(store, task_id)
    state = TaskState(task["state"])
    if state not in {TaskState.RUNNING, TaskState.REVIEW}:
        raise HTTPException(
            status_code=409, detail=f"task {task_id} is {task['state']}; only a running task completes"
        )
    if payload.run_id and task["run_id"] and payload.run_id != task["run_id"]:
        raise HTTPException(
            status_code=409,
            detail=f"run {payload.run_id} does not belong to task {task_id} (its run is {task['run_id']})",
        )
    if payload.run_id:
        other = store.rows("SELECT id FROM tasks WHERE run_id = ? AND id != ?", (payload.run_id, task_id))
        if other:
            raise HTTPException(
                status_code=409,
                detail=f"run {payload.run_id} belongs to task {other[0]['id']}, not to {task_id}",
            )
    import json

    gate = json.loads(task["acceptance"] or "{}")
    if gate.get("requires_artifact") and not payload.proof:
        raise HTTPException(
            status_code=409,
            detail=(
                f"task {task_id} requires evidence and none was given; 'the model said done' is not "
                "a gate (BOOK §3.15/§81)"
            ),
        )
    store.append(
        store.new_event(
            "task.completed",
            {
                "task_id": task_id,
                "run_id": payload.run_id or task["run_id"],
                "proof": payload.proof,
                "artifacts": payload.artifacts,
                "note": payload.note,
            },
            task_id=task_id,
            mission_id=task["mission_id"],
            run_id=payload.run_id or task["run_id"],
            agent_id=task["owner_agent"],
        )
    )
    released = _sync_states(request, task["mission_id"])
    tasks, edges = _graph(store, task["mission_id"])
    view = _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))
    view["released"] = released
    return view


@router.post("/v1/tasks/{task_id}/review")
def review_task(task_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    _publish(request, "task.review_requested", {}, task=task)
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/block")
def block_task(task_id: str, payload: ReasonIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    _publish(request, "task.blocked", {"reason": payload.reason}, task=task)
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/fail")
def fail_task(task_id: str, payload: ReasonIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    _publish(request, "task.failed", {"reason": payload.reason}, task=task)
    _sync_states(request, task["mission_id"])
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, payload: ReasonIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    _publish(request, "task.cancelled", {"reason": payload.reason}, task=task)
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))


@router.post("/v1/tasks/{task_id}/update")
def update_task(task_id: str, payload: UpdateIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    body: dict[str, Any] = {"task_id": task_id}
    if payload.title is not None:
        body["title"] = payload.title
    if payload.description is not None:
        body["description"] = payload.description
    if payload.workspace_scope is not None:
        body["workspace_scope"] = payload.workspace_scope
    _publish(request, "task.updated", body, task=task)
    tasks, edges = _graph(store, task["mission_id"])
    return _view(_task_or_404(store, task_id), edges.get(task_id, []), _states(tasks))
