"""Sandbox providers: the mechanism that can actually refuse an action (M3).

`RuntimeAdapter` never learns what a container is; a provider never learns what a session is. What
crosses the boundary is a :class:`SandboxPlan`: how to prefix the runtime's argv, where it runs,
what environment it gets, and — the part that matters — **what was verified**.

Three providers, in order of strength:

* :class:`ContainerSandboxProvider` — `docker`/`podman run` with only the workspace mounted and
  `--network none` unless asked otherwise. The strongest, and it needs a reachable daemon.
* :class:`NamespaceSandboxProvider` — `bubblewrap`: a user + mount namespace where only the approved
  paths exist. Unprivileged, so it works without a daemon, without a group membership and without
  `sudo`, which is why it is the one this project can prove on an ordinary machine.
* :class:`LocalSandboxProvider` — no isolation at all. It exists so that a missing sandbox degrades
  the product instead of blocking it, and it always reports `weak` with the reason.

A provider that cannot be used says why, in the same shape as one that can: `available()` returns a
verdict, not a boolean, because "docker is installed but its socket is not reachable by this user"
is a different fact from "docker is not installed", and the record keeps them apart.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from metaharness_contracts import Enforcement, EvidenceCheck

#: Paths a process needs to run at all. Bound read-only; never the whole filesystem.
BASE_READONLY: tuple[str, ...] = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ssl",
    "/etc/ca-certificates",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
    "/etc/localtime",
    "/etc/alternatives",
)

#: Inside the sandbox, the runtime's HOME is this path and it contains only what was staged.
SANDBOX_HOME = "/home/sandbox"


class SandboxError(RuntimeError):
    """The sandbox could not be prepared."""


@dataclass
class Availability:
    """Whether a provider can be used, and the fact behind the answer."""

    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail}


@dataclass
class SandboxSpec:
    """What the caller wants isolated. Deliberately small."""

    workspace: str
    network: bool = False
    #: Host paths the runtime itself needs (its install directory), bound read-only.
    runtime_paths: list[str] = field(default_factory=list)
    #: A staged directory that becomes the sandbox's HOME. Never the real one.
    staged_home: str | None = None
    #: Host trees mounted read-only *inside* the staged home, at a relative path. This is how an
    #: install stays where it is while the data root that names it is a per-session copy.
    staged_mirrors: list[tuple[str, str]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: A host-only file the probe tries to read inside the sandbox. It must fail.
    canary: str | None = None
    #: Host paths that must NOT exist inside. The repository is the obvious one: it is what the M2
    #: agent found and started working in.
    forbidden_paths: list[str] = field(default_factory=list)
    #: Host HOME paths that must not be readable inside. A synthetic `/home/<user>` shell is created
    #: by the bind chain, so "does /home exist" measures nothing -- the content is what matters.
    home_probes: list[str] = field(default_factory=list)


@dataclass
class SandboxPlan:
    provider: str
    argv_prefix: list[str]
    cwd: str
    env: dict[str, str]
    filesystem_mode: str
    network_mode: str
    checks: list[EvidenceCheck] = field(default_factory=list)
    note: str | None = None
    cleanup_paths: list[str] = field(default_factory=list)

    @property
    def filesystem(self) -> Enforcement:
        return {
            "container_mount": Enforcement.STRONG,
            "namespace_bind": Enforcement.STRONG,
            "cwd_only": Enforcement.WEAK,
        }.get(self.filesystem_mode, Enforcement.WEAK)

    @property
    def network(self) -> Enforcement:
        return Enforcement.WEAK if self.network_mode != "deny" else Enforcement.STRONG

    def wrap(self, argv: Sequence[str]) -> list[str]:
        # `argv[0]` is resolved here because inside the sandbox the PATH is the sandbox's, not the
        # daemon's: a bare `hermes` that the daemon finds would not be found by the sandbox, and
        # whichever one it did find (a virtualenv's own launcher, whose interpreter may not exist
        # there) is not the one the daemon meant.
        if argv and not Path(argv[0]).is_absolute():
            resolved = shutil.which(argv[0])
            if resolved:
                argv = [resolved, *argv[1:]]
        return [*self.argv_prefix, *argv]


class SandboxProvider(Protocol):
    name: str

    def available(self) -> Availability: ...

    def prepare(self, spec: SandboxSpec) -> SandboxPlan: ...

    def cleanup(self, plan: SandboxPlan) -> None: ...


# --------------------------------------------------------------------------------- namespace


class NamespaceSandboxProvider:
    """bubblewrap: a user + mount namespace where only the approved paths exist.

    The host `HOME`, the repository, the user's other projects and everything else are not hidden
    by policy -- they are simply not there. That is the difference between asking an agent to stay
    in a directory and making the directory the only place that exists.
    """

    name = "namespace"

    def __init__(self, *, bwrap: str | None = None) -> None:
        self.bwrap = bwrap or shutil.which("bwrap") or ""

    def available(self) -> Availability:
        if not self.bwrap:
            return Availability(False, "bubblewrap (bwrap) is not installed")
        # The probe must bind the loader as well as /usr: without /lib64 the dynamic loader is
        # missing and *every* binary fails with "No such file or directory", which looks like a
        # broken sandbox and is really a broken probe. (That is exactly what the first version of
        # this method did, and it made the strongest provider look unavailable.)
        argv = [self.bwrap, "--die-with-parent", "--unshare-all", "--proc", "/proc", "--dev", "/dev"]
        for path in BASE_READONLY:
            if Path(path).exists():
                argv += ["--ro-bind", path, path]
        argv += ["--", "/bin/sh", "-c", "echo sandbox-ok"]
        try:
            probe = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return Availability(False, f"bwrap could not be executed: {exc}")
        if probe.returncode != 0 or "sandbox-ok" not in probe.stdout:
            return Availability(
                False, f"bwrap cannot create a namespace: {(probe.stderr or probe.stdout).strip()[:200]}"
            )
        return Availability(True, f"bubblewrap {self._version()} with user+mount namespaces")

    def _version(self) -> str:
        try:
            out = subprocess.run([self.bwrap, "--version"], capture_output=True, text=True, timeout=10)
            return out.stdout.strip().replace("bubblewrap ", "")
        except Exception:  # pragma: no cover
            return "unknown"

    def prepare(self, spec: SandboxSpec) -> SandboxPlan:
        workspace = str(Path(spec.workspace).resolve())
        if not Path(workspace).is_dir():
            raise SandboxError(f"workspace does not exist: {workspace}")
        # No `--new-session`: it calls setsid(), and `ProcessSupervisor` already puts the process in
        # its own session so it can signal the whole group. Calling setsid() on a process that is
        # already a group leader fails, and bwrap exits -- which is exactly what happened the first
        # time this ran through the supervisor instead of through subprocess in a probe.
        argv: list[str] = [self.bwrap, "--die-with-parent", "--unshare-all"]
        # The sandbox does not inherit the daemon's environment. bwrap passes the parent's variables
        # through by default, which leaked the daemon's PATH -- and would have leaked anything else
        # the daemon holds. Everything the runtime needs is passed explicitly below.
        argv.append("--clearenv")
        if spec.network:
            argv.append("--share-net")
        # /proc and /dev are fresh; /tmp is empty and private.
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for path in (*BASE_READONLY, *spec.runtime_paths):
            resolved = Path(path)
            if resolved.exists():
                argv += ["--ro-bind", str(resolved), str(resolved)]
        # The workspace is the only writable host path.
        argv += ["--bind", workspace, workspace]
        env = dict(spec.env)
        if spec.staged_home:
            # Writable on purpose: this is a copy inside the data root, and a runtime that keeps
            # state needs a HOME it can write to. The real home is never mounted.
            argv += ["--bind", str(Path(spec.staged_home).resolve()), SANDBOX_HOME]
            for source, relative in spec.staged_mirrors:
                source_path = Path(source)
                if source_path.exists():
                    # A writable tmpfs holding read-only binds of the contents. The runtime keeps its
                    # leases and scratch inside this tree, and mounting the directory itself read-only
                    # made it fail with EROFS on its first write (the lease). The tmpfs gives it
                    # somewhere to write that dies with the session, while the install it reads from
                    # is untouched -- copying 830 MB per session was the alternative.
                    target = f"{SANDBOX_HOME}/{relative}"
                    argv += ["--tmpfs", target]
                    for entry in sorted(source_path.iterdir()):
                        if entry.name == ".leases":
                            # The runtime's own lease bookkeeping, and the one thing it must be able
                            # to write here. Mounting the host's copy read-only made hermes fail with
                            # EROFS the first time an unsandboxed session had created one. The tmpfs
                            # gives it a fresh, writable place that dies with the session.
                            continue
                        argv += ["--ro-bind", str(entry.resolve()), f"{target}/{entry.name}"]
            env["HOME"] = SANDBOX_HOME
            env["XDG_CONFIG_HOME"] = f"{SANDBOX_HOME}/.config"
            env["XDG_DATA_HOME"] = f"{SANDBOX_HOME}/.local/share"
        else:
            argv += ["--tmpfs", SANDBOX_HOME]
            env["HOME"] = SANDBOX_HOME
        # A PATH is still needed: the runtime spawns its own children (its tools, its interpreter).
        env.setdefault("PATH", f"{SANDBOX_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin")
        argv += ["--chdir", workspace]
        for key, value in env.items():
            argv += ["--setenv", key, value]
        argv += ["--"]
        plan = SandboxPlan(
            provider=self.name,
            argv_prefix=argv,
            cwd=workspace,
            env=env,
            filesystem_mode="namespace_bind",
            network_mode="unrestricted" if spec.network else "deny",
            note=f"bubblewrap {self._version()}: only the workspace and the runtime's own paths exist",
        )
        plan.checks = self._probe(plan, spec)
        return plan

    def _probe(self, plan: SandboxPlan, spec: SandboxSpec) -> list[EvidenceCheck]:
        """Run the checks *inside* the sandbox. A claim without a check is not evidence."""
        checks: list[EvidenceCheck] = []

        def inside(script: str) -> tuple[int, str]:
            result = subprocess.run(
                [*plan.argv_prefix, "sh", "-c", script], capture_output=True, text=True, timeout=60
            )
            return result.returncode, (result.stdout + result.stderr).strip()

        for forbidden in spec.forbidden_paths:
            code, out = inside(f"test -e {forbidden!r} && echo PRESENT || echo ABSENT")
            checks.append(
                EvidenceCheck(
                    name=f"forbidden_absent:{forbidden}",
                    ok="ABSENT" in out,
                    detail=f"{forbidden} inside the sandbox: {out[:60]}",
                )
            )
        for probe_path in spec.home_probes:
            # Probe *files*, not directories. A bind chain creates a synthetic `/home/<user>/...`
            # shell, so "does this directory exist" is always yes and measures nothing; what matters
            # is whether the user's actual files can be read.
            code, out = inside(f"cat {probe_path!r} 2>&1 | head -1")
            unreadable = any(
                marker in out for marker in ("No such file", "Permission denied", "Is a directory", "cannot open")
            )
            checks.append(
                EvidenceCheck(
                    name=f"home_file_hidden:{probe_path}",
                    ok=unreadable,
                    detail=out[:110] or "read returned nothing",
                )
            )
        if spec.canary:
            code, out = inside(f"cat {spec.canary!r} 2>&1 | head -1")
            checks.append(
                EvidenceCheck(
                    name="canary_unreadable",
                    ok="No such file" in out or "Permission denied" in out or "cannot open" in out,
                    detail=out[:120],
                )
            )
        code, out = inside("touch .sandbox-write-check && echo WRITABLE && rm -f .sandbox-write-check")
        checks.append(
            EvidenceCheck(name="workspace_writable", ok="WRITABLE" in out, detail=out[:80])
        )
        # The network is *measured*, not read off the plan. Run14 had a runtime answer while the
        # policy said the network was closed, and a plan saying "deny" is only a claim about what was
        # configured -- what matters is whether packets actually leave. Two probes, because either
        # alone can mislead: an empty routing table inside the namespace, and a real connect attempt.
        code, out = inside("cat /proc/net/route 2>/dev/null | wc -l")
        routes = max(int(out.strip()) - 1, 0) if out.strip().isdigit() else 0
        connect_code, _ = inside(
            "python3 -c \"import socket;socket.create_connection(('1.1.1.1',443),3)\" 2>/dev/null"
        )
        reachable = connect_code == 0 or routes > 0
        detail = f"routes={routes} connect={'reachable' if connect_code == 0 else 'failed'}"
        checks.append(
            EvidenceCheck(
                name="network_egress_blocked" if plan.network_mode == "deny" else "network_egress_open",
                ok=(not reachable) if plan.network_mode == "deny" else reachable,
                detail=detail,
            )
        )
        return checks

    def cleanup(self, plan: SandboxPlan) -> None:
        return None


# --------------------------------------------------------------------------------- container


class ContainerSandboxProvider:
    """`docker`/`podman run` with only the workspace mounted.

    Implemented, and honest about its own reachability: on the machine where M3 was built the docker
    socket is root-only, so this provider reports `available: false` with that exact reason and the
    namespace provider is used instead. The argv it builds is unit-tested; its execution is not
    claimed as verified here.
    """

    name = "container"

    def __init__(self, *, runtime: str = "docker", image: str = "python:3.12-slim") -> None:
        self.runtime = runtime if shutil.which(runtime) else ""
        self.image = image

    def available(self) -> Availability:
        if not self.runtime:
            return Availability(False, "docker/podman is not installed")
        probe = subprocess.run(
            [self.runtime, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=30
        )
        if probe.returncode != 0:
            reason = (probe.stderr or probe.stdout).strip().splitlines()
            return Availability(
                False,
                f"{self.runtime} is installed but its daemon is not reachable: "
                f"{reason[0][:160] if reason else 'no detail'}",
            )
        return Availability(True, f"{self.runtime} {probe.stdout.strip()} with image {self.image}")

    def prepare(self, spec: SandboxSpec) -> SandboxPlan:
        workspace = str(Path(spec.workspace).resolve())
        argv = [
            self.runtime,
            "run",
            "--rm",
            "--interactive",
            "--workdir",
            workspace,
            "--network",
            "bridge" if spec.network else "none",
            "--mount",
            f"type=bind,src={workspace},dst={workspace}",
        ]
        for path in spec.runtime_paths:
            resolved = Path(path)
            if resolved.exists():
                argv += ["--mount", f"type=bind,src={resolved},dst={resolved},readonly"]
        env = dict(spec.env)
        if spec.staged_home:
            argv += ["--mount", f"type=bind,src={Path(spec.staged_home).resolve()},dst=/home/sandbox"]
            for source, relative in spec.staged_mirrors:
                source_path = Path(source)
                if source_path.exists():
                    target = f"/home/sandbox/{relative}"
                    argv += ["--tmpfs", target]
                    for entry in sorted(source_path.iterdir()):
                        if entry.name == ".leases":
                            continue
                        argv += ["--mount", f"type=bind,src={entry.resolve()},dst={target}/{entry.name},readonly"]
            env["HOME"] = "/home/sandbox"
        for key, value in env.items():
            argv += ["--env", f"{key}={value}"]
        argv += [self.image]
        plan = SandboxPlan(
            provider=self.name,
            argv_prefix=argv,
            cwd=workspace,
            env=env,
            filesystem_mode="container_mount",
            network_mode="bridge" if spec.network else "deny",
            note=f"{self.runtime} container, only the workspace mounted",
        )
        # No execution probe here: if the daemon is unreachable, `prepare` is never reached.
        plan.checks = [
            EvidenceCheck(name="provider_reachable", ok=True, detail=f"{self.runtime} daemon answered")
        ]
        return plan

    def cleanup(self, plan: SandboxPlan) -> None:
        return None


# -------------------------------------------------------------------------------------- none


class LocalSandboxProvider:
    """No isolation. It exists so a missing sandbox degrades the product instead of blocking it."""

    name = "none"

    def available(self) -> Availability:
        return Availability(True, "no sandbox provider: the session runs with a working directory only")

    def prepare(self, spec: SandboxSpec) -> SandboxPlan:
        workspace = str(Path(spec.workspace).resolve())
        plan = SandboxPlan(
            provider=self.name,
            argv_prefix=[],
            cwd=workspace,
            env=dict(spec.env),
            filesystem_mode="cwd_only",
            network_mode="unrestricted" if spec.network else "deny",
            note=(
                "no sandbox: the working directory is a convention, not a boundary. An agent that "
                "tries to leave it can, and the evidence says weak."
            ),
        )
        checks = [
            EvidenceCheck(
                name="no_isolation",
                ok=False,
                detail="nothing enforces the workspace; the record says weak on purpose",
            )
        ]
        for forbidden in spec.forbidden_paths:
            visible = Path(forbidden).exists()
            checks.append(
                EvidenceCheck(
                    name=f"forbidden_absent:{forbidden}",
                    ok=not visible,
                    detail=(
                        f"{forbidden} is {'VISIBLE' if visible else 'absent'} with no sandbox -- "
                        "an agent that looks for it will find it"
                    ),
                )
            )
        plan.checks = checks
        return plan

    def cleanup(self, plan: SandboxPlan) -> None:
        return None


# ---------------------------------------------------------------------------------- selection


@dataclass
class ProviderChoice:
    provider: SandboxProvider
    requested: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"requested": self.requested, "chosen": self.provider.name, "detail": self.detail}


def select_provider(prefer: str = "auto", *, container: str = "docker") -> ProviderChoice:
    """Pick the strongest provider that can actually be used, and say why.

    `prefer` is honoured when the named provider is usable. `auto` walks the ladder from strongest to
    weakest. A provider that is installed but unusable is not chosen, and its reason is kept.
    """
    candidates: list[SandboxProvider] = [
        ContainerSandboxProvider(runtime=container),
        NamespaceSandboxProvider(),
        LocalSandboxProvider(),
    ]
    by_name = {candidate.name: candidate for candidate in candidates}
    if prefer != "auto":
        chosen = by_name.get(prefer)
        if chosen is None:
            raise SandboxError(f"unknown sandbox provider {prefer!r}; one of {sorted(by_name)}")
        verdict = chosen.available()
        if not verdict.ok:
            raise SandboxError(f"sandbox {prefer!r} is not usable: {verdict.detail}")
        return ProviderChoice(chosen, prefer, verdict.detail)
    reasons: list[str] = []
    for candidate in candidates:
        verdict = candidate.available()
        if verdict.ok:
            detail = verdict.detail
            if reasons:
                detail = f"{detail} (rejected first: {'; '.join(reasons)})"
            return ProviderChoice(candidate, prefer, detail)
        reasons.append(f"{candidate.name}: {verdict.detail}")
    # LocalSandboxProvider always reports available, so this is unreachable in practice.
    return ProviderChoice(LocalSandboxProvider(), prefer, "; ".join(reasons))


def describe_providers(*, container: str = "docker") -> list[dict[str, Any]]:
    """Every provider and its verdict, for the UI: a user must be able to see *why* it is weak."""
    rows = []
    for candidate in (
        ContainerSandboxProvider(runtime=container),
        NamespaceSandboxProvider(),
        LocalSandboxProvider(),
    ):
        verdict = candidate.available()
        rows.append({"provider": candidate.name, "available": verdict.ok, "detail": verdict.detail})
    return rows


#: Home-level directories that hold the user's data, never a runtime's installation. A path inside
#: one of these is only bound when it is the runtime's own app tree (`~/.hermes/hermes-agent`).
HOME_DATA_ROOTS = frozenset(
    {
        ".hermes",
        ".config",
        ".ssh",
        ".gnupg",
        ".aws",
        ".kube",
        ".mozilla",
        ".password-store",
        ".local",
        ".cache",
    }
)


def resolve_runtime_paths(binary: str) -> list[str]:
    """The install trees a runtime binary needs, so it can run inside the namespace.

    Resolved from the binary itself rather than assumed, because the interesting case is a launcher
    script: `hermes` is a shell script whose interpreter lives in `~/.hermes/tools/python-3.14...`,
    and binding only the script's own directory produced `exit 127` inside the sandbox. A launcher
    names what it needs by absolute path, so those paths are read and their trees bound -- and
    nothing else is.

    Only paths under the user's home are considered: system paths are already bound read-only by the
    provider, and anything else would widen the boundary for no reason.
    """
    import re

    candidate = Path(binary)
    if candidate.is_absolute() and candidate.exists():
        found = str(candidate)
    else:
        found = shutil.which(binary) or ""
    if not found:
        return []
    home = Path.home()
    paths: list[str] = []

    def add(path: Path) -> None:
        text = str(path)
        if path.name in HOME_DATA_ROOTS:
            return  # a home-level data root is never mounted wholesale
        if text.startswith(str(home)) and text not in paths and Path(text).exists():
            paths.append(text)

    real = Path(found).resolve()
    add(real.parent)
    add(real.parent.parent)
    add(real.parent.parent.parent)
    # What a runtime needs is named somewhere, and it is not always the launcher: `hermes` keeps the
    # path of its committed dependency environment in `installs/<key>/facts.json`. Reading the files
    # that name paths is how the boundary stays derived rather than guessed -- a hardcoded list would
    # be wrong the first time an install moved.
    state_files = [*sorted(home.glob(".hermes/installs/*/facts.json")), *sorted(home.glob(".hermes/installs/*/pm-runtime/*.json"))]
    for script in [real, Path(found), *state_files]:
        try:
            if not script.is_file() or script.stat().st_size > 512 * 1024:
                continue
            text = script.read_text(errors="ignore")
        except OSError:
            continue
        for match in re.findall(r"(/[^\s\"'`]+)", text):
            target = Path(match)
            if not str(target).startswith(str(home)):
                continue
            parts = target.parts
            if "bin" in parts:
                # A path into a `bin/` belongs to a tree: the interpreter needs its lib/ too.
                add(Path(*parts[: parts.index("bin")]))
            try:
                depth = len(target.relative_to(home).parts)
            except ValueError:
                continue
            if depth >= 2 and target.name not in HOME_DATA_ROOTS:
                # An application's own directory, but never a home-level data root: binding
                # `~/.hermes` would hand over the user's sessions, logs and plugins, while
                # `~/.hermes/hermes-agent` is the runtime's installation and is what it needs.
                add(target.parent)
    for marker in (".local/share/mise", ".asdf", ".nvm", ".local/share/uv"):
        path = home / marker
        if path.is_dir():
            paths.append(str(path))
    return paths


def make_staged_home(root: Path, *, config_dirs: Sequence[tuple[str, str]] = ()) -> Path:
    """Stage the *minimum* config a runtime needs, in its own directory.

    The sandbox's HOME is never the real one. Only the named files are copied, so the agent inside
    cannot read the user's other data by walking `$HOME` -- and the copy is what the runtime
    authenticates with.
    """
    staged = root / "home"
    staged.mkdir(parents=True, exist_ok=True)
    for source, relative in config_dirs:
        source_path = Path(source).expanduser()
        if not source_path.exists():
            continue
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source_path, target)
            os.chmod(target, 0o600)
    return staged
