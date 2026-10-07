"""Work-graph rules, as pure functions over tasks.

They live in the contract rather than in the daemon because they are the part that must not drift:
a cycle is a cycle, and "ready" means the same thing to the API, the projection, the UI and the
replay. Nothing here touches a database or an event bus, so every rule is testable in isolation --
and the daemon is expected to call these rather than restate them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .enums import TaskState

#: A task in one of these states is finished, and its dependents may become ready.
SATISFIED_STATES: frozenset[TaskState] = frozenset({TaskState.DONE})

#: A task in one of these states will never satisfy its dependents.
DEAD_STATES: frozenset[TaskState] = frozenset({TaskState.FAILED, TaskState.CANCELLED})

#: States from which a task may start running.
STARTABLE_STATES: frozenset[TaskState] = frozenset({TaskState.READY, TaskState.DRAFT})


class TaskGraphError(ValueError):
    """A graph rule was violated. The API turns this into a 4xx, never a 500."""


def find_cycle(dependencies: Mapping[str, Sequence[str]]) -> list[str] | None:
    """Return one cycle as a path, or ``None`` when the graph is a DAG.

    Deterministic: nodes and edges are visited in sorted order, so the same graph always reports
    the same cycle. A dependency on a task that is not in the mapping is not a cycle here -- the
    caller checks existence separately, because the two errors deserve different messages.
    """
    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def walk(node: str) -> list[str] | None:
        visited.add(node)
        stack.append(node)
        on_stack.add(node)
        for dep in sorted(dependencies.get(node, ())):
            if dep not in dependencies:
                continue
            if dep in on_stack:
                return [*stack[stack.index(dep) :], dep]
            if dep not in visited:
                found = walk(dep)
                if found:
                    return found
        stack.pop()
        on_stack.discard(node)
        return None

    for node in sorted(dependencies):
        if node not in visited:
            cycle = walk(node)
            if cycle:
                return cycle
    return None


def missing_dependencies(
    task_id: str, dependencies: Sequence[str], known: Iterable[str]
) -> list[str]:
    """Dependencies that name a task this graph does not have."""
    known_set = set(known)
    return [dep for dep in dependencies if dep not in known_set and dep != task_id]


def is_ready(state: TaskState, dependencies: Sequence[str], states: Mapping[str, TaskState]) -> bool:
    """A task is ready when every dependency it has is satisfied.

    Dependencies that are missing count as unsatisfied: "ready" is a claim about the graph, and a
    dangling edge cannot support it.
    """
    if state in {TaskState.DONE, TaskState.CANCELLED, TaskState.FAILED}:
        return False
    for dep in dependencies:
        dep_state = states.get(dep)
        if dep_state is None or dep_state not in SATISFIED_STATES:
            return False
    return True


def ready_ids(states: Mapping[str, TaskState], dependencies: Mapping[str, Sequence[str]]) -> list[str]:
    """Every task that should currently be ready, in deterministic order."""
    return sorted(
        task_id
        for task_id, state in states.items()
        if is_ready(state, dependencies.get(task_id, ()), states)
        and state is not TaskState.RUNNING
        and state is not TaskState.REVIEW
    )


def blocked_ids(states: Mapping[str, TaskState], dependencies: Mapping[str, Sequence[str]]) -> list[str]:
    """Tasks waiting on a dependency that failed or was cancelled -- they will never be ready."""
    blocked: list[str] = []
    for task_id, state in sorted(states.items()):
        if state in {TaskState.DONE, TaskState.CANCELLED, TaskState.FAILED}:
            continue
        for dep in dependencies.get(task_id, ()):
            if states.get(dep) in DEAD_STATES:
                blocked.append(task_id)
                break
    return blocked

# ------------------------------------------------------------------------------------------------
# The board: how a canonical task is placed, and why a task is not ready
# ------------------------------------------------------------------------------------------------

#: The lanes a board shows, in the order a board shows them. These are presentation names; the
#: canonical ``TaskState`` values are never renamed on the wire.
LANES: tuple[str, ...] = ("BACKLOG", "READY", "RUNNING", "WAITING", "REVIEW", "BLOCKED", "DONE")

#: Terminal lanes that must stay visible instead of being folded into ``DONE``: a failed or
#: cancelled task is not finished work, and a board that hides it is lying by omission.
TERMINAL_LANES: tuple[str, ...] = ("FAILED", "CANCELLED")

#: Canonical state -> lane. ``claimed`` shares READY because the claim is already visible on the
#: card as the canonical state; a separate lane would invent a state the contract does not have.
LANE_OF_STATE: dict[TaskState, str] = {
    TaskState.DRAFT: "BACKLOG",
    TaskState.READY: "READY",
    TaskState.CLAIMED: "READY",
    TaskState.RUNNING: "RUNNING",
    TaskState.WAITING: "WAITING",
    TaskState.REVIEW: "REVIEW",
    TaskState.BLOCKED: "BLOCKED",
    TaskState.DONE: "DONE",
    TaskState.FAILED: "FAILED",
    TaskState.CANCELLED: "CANCELLED",
}


def board_lane(state: TaskState) -> str:
    """Which lane a task belongs in. Total by construction: an unknown state raises, never guesses.

    The mapping lives here, next to the other graph rules, so the API, the projection and the UI
    cannot drift apart on what ``review`` means.
    """
    try:
        return LANE_OF_STATE[state]
    except KeyError as exc:  # pragma: no cover - a new TaskState must be mapped deliberately
        raise TaskGraphError(f"no board lane is defined for state {state!r}") from exc


def effective_lane(
    task_id: str,
    state: TaskState,
    states: Mapping[str, TaskState],
    dependencies: Mapping[str, Sequence[str]],
) -> str:
    """The lane a task belongs in, with readiness folded into the state.

    A task whose canonical state says it could start, but whose dependencies are not satisfied, is
    not READY: it is WAITING -- or BLOCKED, when a dependency has already failed. A board that puts
    it in READY would be offering work the daemon refuses to start.
    """
    lane = board_lane(state)
    if lane in {"BACKLOG", "READY"} and not is_ready(state, dependencies.get(task_id, ()), states):
        dead = dependency_blockers(task_id, states, dependencies)["dead"]
        return "BLOCKED" if dead else "WAITING"
    return lane


def waves(dependencies: Mapping[str, Sequence[str]]) -> list[list[str]]:
    """Execution layers of the graph, as a list of waves in deterministic order.

    Wave 0 holds tasks with no dependencies; a task sits one wave past its deepest dependency. The
    layering is structural -- it describes the graph, not the current states -- so the same graph
    always produces the same waves, which is what makes it safe to draw and safe to assert on.

    A dependency naming a task outside the mapping is ignored here (``missing_dependencies`` reports
    it separately), and a cycle is refused rather than laid out.
    """
    if find_cycle(dependencies) is not None:
        raise TaskGraphError("the graph has a cycle; waves are only defined for a DAG")
    depth: dict[str, int] = {}

    def depth_of(node: str, seen: frozenset[str]) -> int:
        if node in depth:
            return depth[node]
        if node in seen:  # pragma: no cover - find_cycle already refused this
            raise TaskGraphError("the graph has a cycle; waves are only defined for a DAG")
        parents = [dep for dep in dependencies.get(node, ()) if dep in dependencies]
        value = 0 if not parents else 1 + max(depth_of(dep, seen | {node}) for dep in parents)
        depth[node] = value
        return value

    for node in sorted(dependencies):
        depth_of(node, frozenset())
    if not depth:
        return []
    layers: list[list[str]] = [[] for _ in range(max(depth.values()) + 1)]
    for node, level in sorted(depth.items()):
        layers[level].append(node)
    return layers


def dependency_blockers(
    task_id: str, states: Mapping[str, TaskState], dependencies: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    """The dependency facts that explain why a task is not ready -- nothing invented.

    ``waiting_on`` are dependencies that are not satisfied yet and could still become so;
    ``dead`` are dependencies that failed or were cancelled, which is a different sentence and a
    different conversation. Only the graph and the canonical states are consulted, so the UI can
    show this without inventing a reason of its own.
    """
    waiting: list[str] = []
    dead: list[str] = []
    for dep in dependencies.get(task_id, ()):
        dep_state = states.get(dep)
        if dep_state is None:
            waiting.append(dep)
        elif dep_state in DEAD_STATES:
            dead.append(dep)
        elif dep_state not in SATISFIED_STATES:
            waiting.append(dep)
    return {"waiting_on": sorted(waiting), "dead": sorted(dead)}

