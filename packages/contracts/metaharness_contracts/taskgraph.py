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
