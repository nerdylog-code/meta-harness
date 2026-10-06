"""Budgets: the control plane enforcing what it can, and recording what it cannot (M3).

Every budget has a mechanism, and the mechanism decides how strong the claim may be:

| budget | mechanism | honest strength |
| --- | --- | --- |
| wall time | the supervisor owns the clock, then kills the tree | **strong** |
| tool calls | counted from real events, then cancel | **moderate** (a call in flight finishes) |
| child processes | descendants counted and killed | **moderate** (a fork can slip between polls) |
| tokens | the provider reports after the turn | **weak** |
| cost | the same | **weak** |

The rule the code follows: **when a limit is reached, the record says what was done about it.** For
wall time that is "cancelled, tree killed, orphans checked". For cost it is "recorded; the provider
reports after the turn, so no interruption was possible". A soft limit labelled soft is useful; a
soft limit labelled hard is a trap, and the contract refuses to store one.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from metaharness_contracts import BudgetKind, BudgetRecord, Enforcement, Provenance, RequestedPolicy

from .sandbox import EnvironmentPlan

POLL_INTERVAL_S = 1.0


class BudgetTracker:
    """Watches one session against its budgets. Owned by the daemon, not by the runtime."""

    def __init__(
        self,
        *,
        session_id: str,
        requested: RequestedPolicy,
        cancel: Callable[[], Any],
        publish: Callable[..., Any],
        pid: int | None = None,
        environment: EnvironmentPlan | None = None,
        on_exceeded: Callable[[dict[str, Any]], None] | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        self.session_id = session_id
        self.requested = requested
        self.cancel = cancel
        self.publish = publish
        self.pid = pid
        self.environment = environment
        self.on_exceeded = on_exceeded
        self.poll_interval_s = poll_interval_s
        self.started_at = time.monotonic()
        self.tool_calls = 0
        self.tokens: float | None = None
        self.cost: float | None = None
        self.child_processes = 0
        self._exceeded: dict[BudgetKind, dict[str, Any]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = False

    # ------------------------------------------------------------------- limits

    def _limit(self, kind: BudgetKind) -> float | None:
        return self.requested.limit_of(kind)

    @property
    def exceeded(self) -> dict[str, Any]:
        return {kind.value: detail for kind, detail in self._exceeded.items()}

    # -------------------------------------------------------------------- start

    async def start(self) -> None:
        if self._limit(BudgetKind.WALL_TIME) is not None:
            self._tasks.append(asyncio.create_task(self._watch_wall_time(), name=f"budget-wall-{self.session_id}"))
        if self._limit(BudgetKind.CHILD_PROCESSES) is not None and self.pid:
            self._tasks.append(
                asyncio.create_task(self._watch_children(), name=f"budget-children-{self.session_id}")
            )

    async def stop(self) -> None:
        self._stopped = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    # ------------------------------------------------------------------ watchers

    async def _watch_wall_time(self) -> None:
        limit = self._limit(BudgetKind.WALL_TIME)
        assert limit is not None
        try:
            await asyncio.sleep(limit)
        except asyncio.CancelledError:
            return
        if self._stopped:
            return
        observed = round(time.monotonic() - self.started_at, 3)
        await self._exceed(
            BudgetKind.WALL_TIME,
            observed=observed,
            action="cancelled: the supervisor killed the tree and checked for orphans",
            kill=True,
        )

    async def _watch_children(self) -> None:
        limit = self._limit(BudgetKind.CHILD_PROCESSES)
        assert limit is not None
        while not self._stopped:
            try:
                await asyncio.sleep(self.poll_interval_s)
            except asyncio.CancelledError:
                return
            count = self._count_descendants()
            self.child_processes = count
            if count > limit:
                await self._exceed(
                    BudgetKind.CHILD_PROCESSES,
                    observed=count,
                    action="cancelled: the supervisor killed the tree and checked for orphans",
                    kill=True,
                )
                return

    def _count_descendants(self) -> int:
        if not self.pid:
            return 0
        try:
            import psutil

            process = psutil.Process(self.pid)
            return len(process.children(recursive=True))
        except Exception:
            return 0

    # ----------------------------------------------------------------- observers

    def observe_tool_call(self) -> None:
        """Called for every real `tool.started` event: the count is events, not intentions."""
        self.tool_calls += 1
        limit = self._limit(BudgetKind.TOOL_CALLS)
        if limit is not None and self.tool_calls >= limit and BudgetKind.TOOL_CALLS not in self._exceeded:
            asyncio.create_task(
                self._exceed(
                    BudgetKind.TOOL_CALLS,
                    observed=float(self.tool_calls),
                    action=(
                        "cancelled after the call in flight: the count is exact, the interruption is "
                        "best effort"
                    ),
                    kill=True,
                )
            )

    def observe_usage(self, sample: Any) -> None:
        """Tokens and cost come from the provider's own sample. No silent estimates."""
        try:
            input_tokens = getattr(getattr(sample, "input_tokens", None), "value", None)
            output_tokens = getattr(getattr(sample, "output_tokens", None), "value", None)
            if input_tokens is not None or output_tokens is not None:
                self.tokens = float(input_tokens or 0) + float(output_tokens or 0)
            cost = getattr(getattr(sample, "provider_cost", None), "value", None)
            if cost is not None:
                self.cost = float(cost)
        except Exception:  # pragma: no cover - a malformed sample is not a budget failure
            return
        for kind, value in ((BudgetKind.TOKENS, self.tokens), (BudgetKind.COST, self.cost)):
            limit = self._limit(kind)
            if limit is None or value is None or kind in self._exceeded:
                continue
            if value >= limit:
                asyncio.create_task(
                    self._exceed(
                        kind,
                        observed=float(value),
                        action=(
                            "recorded after the fact: the provider reports this with the usage sample, "
                            "so the turn could not be interrupted before it was exceeded"
                        ),
                        kill=False,
                    )
                )

    # ------------------------------------------------------------------ exceeded

    async def _exceed(
        self, kind: BudgetKind, *, observed: float, action: str, kill: bool
    ) -> None:
        if kind in self._exceeded:
            return
        limit = self._limit(kind)
        record = self._record(kind)
        mode = record.enforcement_mode if record else "none"
        enforcement = record.enforcement if record else Enforcement.WEAK
        detail = {
            "session_id": self.session_id,
            "kind": kind.value,
            "requested": limit,
            "observed": observed,
            "enforcement_mode": mode,
            "enforcement": enforcement.value,
            "action": action,
            "interrupted": kill,
        }
        self._exceeded[kind] = detail
        survivors: list[int] = []
        if kill:
            try:
                await self.cancel()
            except Exception as exc:  # a failed cancel must be visible, not swallowed
                detail["cancel_error"] = str(exc)
        if self.pid:
            try:
                import psutil

                survivors = [
                    child.pid
                    for child in psutil.Process(self.pid).children(recursive=True)
                    if child.is_running()
                ]
            except Exception:
                survivors = []
        detail["survivors"] = survivors
        if survivors:
            # A budget that stopped the session but left processes running did not stop the session.
            detail["action"] = f"{action}; WARNING: {len(survivors)} process(es) survived"
        self.publish("budget.exceeded", detail, method="measured", session_id=self.session_id)
        if self.on_exceeded is not None:
            self.on_exceeded(detail)

    # ------------------------------------------------------------------- records

    def _record(self, kind: BudgetKind) -> BudgetRecord | None:
        if self.environment is None:
            return None
        return self.environment.effective.budget(kind)

    def records(self) -> list[BudgetRecord]:
        """The budgets as they stand now: requested, observed, how enforced, and what happened."""
        out: list[BudgetRecord] = []
        for request in self.requested.budgets:
            template = self._record(request.kind)
            observed: float | None = {
                BudgetKind.WALL_TIME: round(time.monotonic() - self.started_at, 3),
                BudgetKind.TOOL_CALLS: float(self.tool_calls),
                BudgetKind.TOKENS: self.tokens,
                BudgetKind.COST: self.cost,
                BudgetKind.CHILD_PROCESSES: float(self.child_processes),
            }.get(request.kind)
            detail = self._exceeded.get(request.kind)
            out.append(
                BudgetRecord(
                    kind=request.kind,
                    requested=request.limit,
                    observed=observed,
                    enforcement_mode=template.enforcement_mode if template else "none",
                    enforcement=template.enforcement if template else Enforcement.WEAK,
                    limit_reached=detail is not None,
                    action=(detail or {}).get("action"),
                    provenance=(
                        Provenance.PROVIDER_REPORTED
                        if request.kind in {BudgetKind.TOKENS, BudgetKind.COST} and observed is not None
                        else Provenance.MEASURED
                    ),
                    note=request.note,
                )
            )
        return out
