"""ExecutionEnvironment: the adapter asks for an environment, never for a container (M3).

This is the seam the Architect drew:

    RuntimeAdapter -> ExecutionEnvironment -> SandboxProvider -> ProcessSupervisor

The adapter hands over what it needs to run (a workspace, whether the network is needed, its own
install paths); the environment picks a provider, prepares the plan, runs the probe checks and
returns both the argv to spawn and the *effective* policy with the evidence behind it. The adapter
never sees `bwrap` or `docker`, and the provider never sees a session.

The checks are run before the session starts, not after: a boundary that is only asserted is a
boundary that does not exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from metaharness_contracts import (
    BudgetKind,
    BudgetRecord,
    EffectivePolicy,
    Enforcement,
    EnforcementEvidence,
    EvidenceCheck,
    Provenance,
    RequestedPolicy,
)
from metaharness_contracts.enums import Enforcement as _Enforcement  # noqa: F401  (typing clarity)

from .provider import (
    SANDBOX_HOME,
    Availability,
    LocalSandboxProvider,
    ProviderChoice,
    SandboxError,
    SandboxPlan,
    SandboxProvider,
    SandboxSpec,
    describe_providers,
    make_staged_home,
    resolve_runtime_paths,
    select_provider,
)

#: Which enforcement level each budget kind can reach, and by what mechanism. The table is the
#: honest answer to "can this be a hard limit?" -- and for tokens/cost the answer is usually no.
BUDGET_MODES: dict[BudgetKind, tuple[str, Enforcement, str]] = {
    BudgetKind.WALL_TIME: (
        "process_supervisor",
        Enforcement.STRONG,
        "the supervisor owns the clock and kills the tree when it expires",
    ),
    BudgetKind.TOOL_CALLS: (
        "event_counter",
        Enforcement.MODERATE,
        "counted from real tool events and cancelled at the limit; a call already in flight finishes",
    ),
    BudgetKind.CHILD_PROCESSES: (
        "process_tree_watch",
        Enforcement.MODERATE,
        "descendants counted and killed; a process can fork between two observations",
    ),
    BudgetKind.TOKENS: (
        "provider_reported",
        Enforcement.WEAK,
        "the provider reports tokens after the turn: the limit is recorded, not enforced mid-turn",
    ),
    BudgetKind.COST: (
        "provider_reported",
        Enforcement.WEAK,
        "cost arrives with the usage sample, after the turn: a soft limit, and labelled soft",
    ),
}


@dataclass
class EnvironmentPlan:
    """What the adapter needs, plus what the record must say."""

    plan: SandboxPlan
    effective: EffectivePolicy
    evidence: EnforcementEvidence
    choice: ProviderChoice
    staged_home: str | None = None

    def wrap(self, argv: Sequence[str]) -> list[str]:
        return self.plan.wrap(argv)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sandbox": self.choice.as_dict(),
            "effective": self.effective.model_dump(mode="json"),
            "evidence": self.evidence.model_dump(mode="json"),
        }


class ExecutionEnvironment:
    """Prepares a sandboxed environment for one session and reports what it achieved."""

    def __init__(
        self,
        *,
        provider: SandboxProvider | None = None,
        prefer: str = "auto",
        container: str = "docker",
        data_root: str | None = None,
    ) -> None:
        self.provider = provider
        self.prefer = prefer
        self.container = container
        self.data_root = Path(data_root) if data_root else Path.cwd()

    # ------------------------------------------------------------------ providers

    def providers(self) -> list[dict[str, Any]]:
        return describe_providers(container=self.container)

    def _choose(self, prefer: str) -> ProviderChoice:
        if self.provider is not None:
            verdict = self.provider.available()
            return ProviderChoice(self.provider, prefer, verdict.detail)
        return select_provider(prefer, container=self.container)

    # ------------------------------------------------------------------- prepare

    def prepare(
        self,
        *,
        workspace: str,
        requested: RequestedPolicy,
        runtime_binary: str | None = None,
        runtime_paths: list[str] | None = None,
        staged_config: Sequence[tuple[str, str]] = (),
        staged_env: dict[str, str] | None = None,
        staged_mirrors: Sequence[tuple[str, str]] = (),
        env: dict[str, str] | None = None,
        canary_name: str = "canary-host-only.txt",
        runtime_forbidden: Sequence[str] | None = None,
    ) -> EnvironmentPlan:
        """Prepare the environment and verify it. Raises :class:`SandboxError` when a *named*
        provider is unusable; under `auto` it degrades to `none` and says so in the evidence."""
        workspace_path = Path(workspace).expanduser()
        if not workspace_path.is_dir():
            workspace_path.mkdir(parents=True, exist_ok=True)
        workspace = str(workspace_path.resolve())

        choice = self._choose(requested.sandbox)
        network = requested.network == "unrestricted" or requested.network == "allowlist"

        # The canary lives outside the workspace on purpose: if the sandbox can read it, the
        # filesystem boundary is not one.
        canary = self.data_root / canary_name
        try:
            canary.parent.mkdir(parents=True, exist_ok=True)
            canary.write_text("host-only\n", encoding="utf-8")
        except OSError:  # pragma: no cover - read-only data root
            canary = None  # type: ignore[assignment]

        staged_home: Path | None = None
        if staged_config:
            staged_home = make_staged_home(self.data_root / "sandbox", config_dirs=staged_config)

        paths = list(runtime_paths or [])
        if runtime_binary and not paths:
            paths = resolve_runtime_paths(runtime_binary)

        # What must not be visible inside: the repository, and the real HOME's content. The second
        # matters because a bind chain creates a synthetic `/home/<user>`, so "is /home there" is
        # the wrong question -- what matters is whether the user's actual files are.
        forbidden = [str(Path.cwd())]
        try:
            from .. import paths as _paths

            root = _paths.find_repo_root()
            if root:
                forbidden = [str(root)]
        except Exception:  # pragma: no cover - running outside the repo
            pass
        forbidden += list(runtime_forbidden or [])
        # Specific real files, not directories: the ones a leaked sandbox would expose are the
        # user's credentials and shell configuration.
        home = Path.home()
        home_probes = [
            str(path)
            for path in (
                home / ".hermes" / "auth.json",
                home / ".bashrc",
                home / ".ssh" / "id_ed25519",
                home / ".gitconfig",
            )
            if path.exists()
        ]

        # With no sandbox there is no synthetic HOME to point at: a staged `HERMES_HOME` would name a
        # path that does not exist on the host and the runtime would refuse to start -- which is what
        # happened the first time a no-sandbox session ran after staging was introduced. The staged
        # config exists to serve a sandbox; without one the runtime keeps the real home, and the
        # evidence says `weak` because that is what it is.
        sandboxed = choice.provider.name != "none"
        spec = SandboxSpec(
            workspace=workspace,
            network=network,
            runtime_paths=paths,
            staged_home=str(staged_home) if (staged_home and sandboxed) else None,
            staged_mirrors=[(str(a), b) for a, b in staged_mirrors] if sandboxed else [],
            env={
                # A relative value is resolved against the sandbox's own home, so a runtime can be
                # pointed at its staged config (`HERMES_HOME=.hermes`) without naming the sandbox.
                key: (f"{SANDBOX_HOME}/{value}" if not value.startswith("/") else value)
                for key, value in (dict(staged_env or {}) if sandboxed else {}).items()
            }
            | dict(env or {}),
            canary=str(canary) if canary else None,
            forbidden_paths=forbidden,
            home_probes=home_probes,
        )
        plan = choice.provider.prepare(spec)

        checks = list(plan.checks)
        evidence = self._evidence(plan, checks, choice, requested)
        effective = self._effective(plan, requested, workspace)
        return EnvironmentPlan(
            plan=plan,
            effective=effective,
            evidence=evidence,
            choice=choice,
            staged_home=str(staged_home) if staged_home else None,
        )

    # ------------------------------------------------------------------- records

    def _evidence(
        self,
        plan: SandboxPlan,
        checks: list[EvidenceCheck],
        choice: ProviderChoice,
        requested: RequestedPolicy,
    ) -> EnforcementEvidence:
        from .provider import LocalSandboxProvider as _Local

        is_local = plan.provider == _Local.name
        filesystem = plan.filesystem
        network = plan.network
        # A boundary that failed its own check is not a boundary.
        # A boundary that failed one of its own containment checks is not a boundary.
        if any(not check.ok for check in checks if check.name.startswith(("forbidden_absent:", "home_file_hidden:"))):
            filesystem = Enforcement.WEAK
        budgets = {budget.kind: budget for budget in requested.budgets}
        wall_time = BUDGET_MODES[BudgetKind.WALL_TIME][1] if BudgetKind.WALL_TIME in budgets else Enforcement.WEAK
        tool_calls = (
            BUDGET_MODES[BudgetKind.TOOL_CALLS][1] if BudgetKind.TOOL_CALLS in budgets else Enforcement.WEAK
        )
        children = (
            BUDGET_MODES[BudgetKind.CHILD_PROCESSES][1]
            if BudgetKind.CHILD_PROCESSES in budgets
            else Enforcement.WEAK
        )
        note = plan.note
        if is_local:
            note = f"{note} Chosen provider: none ({choice.detail})"
        return EnforcementEvidence(
            provider=plan.provider,
            filesystem=filesystem,
            network=network,
            wall_time=wall_time,
            tool_calls=tool_calls,
            tokens=Enforcement.WEAK,
            cost=Enforcement.WEAK,
            child_processes=children,
            checks=checks,
            note=note,
        )

    def _effective(
        self, plan: SandboxPlan, requested: RequestedPolicy, workspace: str
    ) -> EffectivePolicy:
        records: list[BudgetRecord] = []
        for budget in requested.budgets:
            mode, enforcement, _ = BUDGET_MODES[budget.kind]
            records.append(
                BudgetRecord(
                    kind=budget.kind,
                    requested=budget.limit,
                    observed=0.0,
                    enforcement_mode=mode,
                    enforcement=enforcement,
                    provenance=Provenance.MEASURED,
                    note=budget.note,
                )
            )
        return EffectivePolicy(
            workspace=workspace,
            sandbox_provider=plan.provider,
            filesystem_mode=plan.filesystem_mode,
            network_mode=plan.network_mode,
            workspace_policy=requested.workspace_policy,
            budgets=records,
        )

    # ------------------------------------------------------------------- cleanup

    def cleanup(self, environment: EnvironmentPlan) -> None:
        try:
            self.provider.cleanup(environment.plan) if self.provider else None
        except Exception:  # pragma: no cover - cleanup must not raise
            pass
        if environment.staged_home:
            import shutil

            shutil.rmtree(environment.staged_home, ignore_errors=True)
