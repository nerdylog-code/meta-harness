"""Runtime conformance suite (BOOK §69), as code rather than as a checklist.

Every adapter — fake, Pi, Hermes, OpenClaw, OMP — runs this. It asserts the
properties the rest of the system silently depends on, and it is the mechanism
that turns "we should check that" into a failing test.

The suite is deliberately strict about the difference between *absent* and
*empty*: an unsupported capability must raise :class:`UnsupportedCapability`, and
a measurement that was not taken must be `unknown`, not `0`.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from .enums import CAP_SESSION_STEER, CAP_USAGE_TOKENS, Provenance, UnsupportedCapability
from .ids import IdKind, new_id
from .runtime import ADAPTER_METHODS, Message, RuntimeAdapter, SessionSpec
from .usage import UsageSample


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ConformanceReport:
    runtime_id: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "ok": self.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }

    def summary(self) -> str:
        passed = sum(1 for check in self.checks if check.ok)
        head = f"conformance {self.runtime_id}: {passed}/{len(self.checks)} ok"
        if self.ok:
            return head
        return head + "\nfailures:\n" + "\n".join(f"  - {c.name}: {c.detail}" for c in self.failures)


def check_protocol_shape(adapter: object) -> ConformanceReport:
    """Static half: the adapter exposes the whole interface, not most of it."""
    report = ConformanceReport(runtime_id=getattr(adapter, "runtime_id", "unknown"))
    for method in ADAPTER_METHODS:
        present = callable(getattr(adapter, method, None))
        report.add(f"implements {method}", present, "" if present else "missing method")
    report.add(
        "is a RuntimeAdapter (runtime_checkable)",
        isinstance(adapter, RuntimeAdapter),
        "protocol mismatch",
    )
    return report


async def run_conformance(
    adapter: RuntimeAdapter,
    *,
    agent_id: str | None = None,
    expect_unsupported: str = CAP_SESSION_STEER,
) -> ConformanceReport:
    """Drive a real adapter end to end, offline."""
    report = check_protocol_shape(adapter)
    runtime_id = getattr(adapter, "runtime_id", "unknown")
    agent = agent_id or new_id(IdKind.AGENT)

    # 1. probe
    try:
        info = await adapter.probe()
        report.add("probe returns RuntimeInfo", info.runtime_id == runtime_id, f"got {info.runtime_id!r}")
        report.add("probe reports availability", isinstance(info.available, bool), repr(info.available))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        report.add("probe returns RuntimeInfo", False, f"{type(exc).__name__}: {exc}")
        return report

    # 2. capability negotiation
    try:
        capabilities = await adapter.capabilities()
        report.add("capabilities returns a CapabilitySet", hasattr(capabilities, "supports"), type(capabilities).__name__)
        report.add(
            "capability ids are dotted and versionable",
            all(("." in key and info.version >= 1) for key, info in capabilities.capabilities.items()),
            str(sorted(capabilities.capabilities)),
        )
        report.add(
            "capabilities carry no vendor-specific schema branch",
            all(isinstance(info.supported, bool) for info in capabilities.capabilities.values()),
            "",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("capabilities returns a CapabilitySet", False, f"{type(exc).__name__}: {exc}")
        return report

    # 3. a session without an explicit tool restriction must be refused
    try:
        await adapter.create_session(
            SessionSpec(agent_id=agent, runtime_id=runtime_id, allowed_tools=None)  # type: ignore[arg-type]
        )
        report.add("session without tool restriction is refused", False, "the call succeeded")
    except Exception:
        report.add("session without tool restriction is refused", True)

    # 4. create, send, stream, usage, cancel, close
    session_id: str | None = None
    try:
        session = await adapter.create_session(
            SessionSpec(agent_id=agent, runtime_id=runtime_id, allowed_tools=[], model="conformance")
        )
        session_id = session.session_id
        report.add("create_session returns a session id", session.session_id.startswith("ses_"), session.session_id)
    except Exception as exc:  # noqa: BLE001
        report.add("create_session returns a session id", False, f"{type(exc).__name__}: {exc}")
        return report

    try:
        await adapter.send(session_id, Message(text="conformance ping"))
        report.add("send accepts a message", True)
    except Exception as exc:  # noqa: BLE001
        report.add("send accepts a message", False, f"{type(exc).__name__}: {exc}")

    try:
        collected = [event async for event in adapter.events(session_id)]
        report.add("events yields canonical envelopes", all(_is_canonical(event) for event in collected), f"{len(collected)} events")
    except Exception as exc:  # noqa: BLE001
        report.add("events yields canonical envelopes", False, f"{type(exc).__name__}: {exc}")

    try:
        sample = await adapter.usage(session_id)
        report.add("usage returns a UsageSample", isinstance(sample, UsageSample), type(sample).__name__)
        report.add(
            "usage carries per-metric provenance",
            all(getattr(sample, name).provenance in tuple(Provenance) for name in sample.METRIC_FIELDS),
            "",
        )
        report.add(
            "unknown is not turned into zero",
            all(
                getattr(sample, name).value is None
                for name in sample.METRIC_FIELDS
                if getattr(sample, name).provenance is Provenance.UNKNOWN
            ),
            "; ".join(f"{name}={getattr(sample, name).value}" for name in sample.unknown_fields[:3]),
        )
        if capabilities.supports(CAP_USAGE_TOKENS):
            report.add(
                "advertised token usage is actually reported",
                sample.input_tokens.known,
                "input_tokens is unknown but usage.tokens was advertised",
            )
    except Exception as exc:  # noqa: BLE001
        report.add("usage returns a UsageSample", False, f"{type(exc).__name__}: {exc}")

    # 5. unsupported capability must refuse, loudly
    if capabilities.supports(expect_unsupported):
        report.add(f"unsupported path ({expect_unsupported})", True, "adapter claims support; nothing to refuse")
    else:
        try:
            await adapter.steer(session_id, "conformance steer")
            report.add(f"unsupported capability raises ({expect_unsupported})", False, "steer returned normally")
        except UnsupportedCapability:
            report.add(f"unsupported capability raises ({expect_unsupported})", True)
        except Exception as exc:  # noqa: BLE001
            report.add(
                f"unsupported capability raises ({expect_unsupported})",
                False,
                f"raised {type(exc).__name__} instead of UnsupportedCapability",
            )

    try:
        await adapter.cancel(session_id)
        report.add("cancel is accepted", True)
    except Exception as exc:  # noqa: BLE001
        report.add("cancel is accepted", False, f"{type(exc).__name__}: {exc}")

    try:
        await adapter.close(session_id)
        report.add("close is accepted", True)
    except Exception as exc:  # noqa: BLE001
        report.add("close is accepted", False, f"{type(exc).__name__}: {exc}")

    return report


def _is_canonical(event: object) -> bool:
    required = {"id", "seq", "ts", "kind", "payload", "provenance"}
    return isinstance(event, object) and required <= set(getattr(event, "model_fields", {})) or required <= set(
        getattr(event, "__dict__", {})
    )


def adapter_is_async(adapter: object) -> dict[str, bool]:
    """Diagnostic: which adapter methods are coroutine functions.

    `events` is a plain method returning an async iterator, so it is expected to
    be a regular function that yields.
    """
    out: dict[str, bool] = {}
    for method in ADAPTER_METHODS:
        attribute = getattr(adapter, method, None)
        if method == "events":
            out[method] = attribute is not None and not inspect.iscoroutinefunction(attribute)
        else:
            out[method] = inspect.iscoroutinefunction(attribute)
    return out
