"""Approvals: proposal ≠ approval ≠ execution ≠ acceptance.

An approval authorises **one exact action**, identified by a hash over the action type and the
payload. Nothing here is a generic permission: the question this API answers is "does this approval
cover *this* action?", and the answer is mechanical -- no LLM, no judgement call, no operator
discretion at the moment of execution.

The rules, and the refusal each one produces:

* an approval is never a blanket authorisation -- it is bound to a payload hash (409 on mismatch);
* changing the payload invalidates it -- a different payload is a different hash (409);
* an expired approval does not execute (409, and the expiry is recorded as an event);
* a denied approval does not execute (409);
* a consumed approval cannot be reused (409) -- consumption is a fact in the log;
* R3 and R4 require a named human approver (409 without one), and R4 can never be delegated
  (BOOK §40/§44);
* a runtime or plugin cannot lower the risk of what it proposes -- a declared floor per action type
  is applied, and a proposal below it is refused (409).

Honest limit: the approver's identity is *named* but not *authenticated*, because the API has no
actor authentication yet. The gate is structural (a name is required and recorded, the payload is
bound, the state is in the log), and the identity claim is what S2 auth has to make verifiable.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from metaharness.auth import actor_from_request, grant_requires_operator
from metaharness_contracts import ApprovalRequest, IdKind, RiskLevel, new_id
from metaharness_contracts.approval import APPROVAL_TTL_S, payload_hash, requires_human

router = APIRouter(tags=["approvals"])

#: The lowest risk an action type may be proposed at. A runtime may propose a *higher* risk (it is
#: being cautious) but never a lower one: that is how a gate gets bypassed by whoever writes the
#: proposal. Prefixes are matched longest-first, so `deploy.production` beats `deploy.`.
RISK_FLOOR: dict[str, RiskLevel] = {
    "file.read": RiskLevel.R0,
    "file.write": RiskLevel.R2,
    "file.delete": RiskLevel.R3,
    "git.commit": RiskLevel.R1,
    "git.push": RiskLevel.R3,
    "git.force_push": RiskLevel.R4,
    "deploy.": RiskLevel.R4,
    "credential.": RiskLevel.R4,
    "spend.": RiskLevel.R4,
    "task.complete": RiskLevel.R2,
}

_ORDER = {
    RiskLevel.R0: 0,
    RiskLevel.R1: 1,
    RiskLevel.R2: 2,
    RiskLevel.R3: 3,
    RiskLevel.R4: 4,
}


def floor_for(action_type: str) -> RiskLevel:
    """The declared floor for an action type, longest matching prefix first."""
    for prefix in sorted(RISK_FLOOR, key=len, reverse=True):
        if action_type == prefix or action_type.startswith(prefix):
            return RISK_FLOOR[prefix]
    return RiskLevel.R0


# --------------------------------------------------------------------------------- payloads


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: str
    human_summary: str
    reversibility: str = "reversible"
    requested_by: str
    ttl_s: float | None = None
    #: Optional scope. Without it an approval is about nothing in particular, which is honest but
    #: useless to a board -- so the caller may name the mission and task it belongs to.
    task_id: str | None = None
    mission_id: str | None = None


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: str = ""


class DenyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: str = ""
    reason: str | None = None


class ConsumeIn(BaseModel):
    """What the caller is about to do. It must hash to what was approved."""

    model_config = ConfigDict(extra="forbid")

    action_type: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
    by: str = ""


# --------------------------------------------------------------------------------- reading


def _store(request: Request):
    return request.app.state.store


def _row_or_404(store, approval_id: str) -> dict[str, Any]:
    rows = store.rows("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no such approval: {approval_id}")
    return rows[0]


def _request_of(row: dict[str, Any]) -> ApprovalRequest:
    """Rebuild the frozen contract object from the log, so the rules are the contract's rules."""
    return ApprovalRequest(
        id=row["id"],
        risk_level=RiskLevel(row["risk_level"]),
        action_type=row["action_type"],
        action_payload_hash=row["action_payload_hash"],
        human_summary=row["human_summary"],
        reversibility=row["reversibility"],
        expires_at=row["expires_at"],
        requested_by=row["requested_by"],
        action_payload=json.loads(row["action_payload"] or "{}"),
        granted=row["state"] in {"granted", "consumed"},
        granted_by=row["granted_by"],
        granted_at=row["granted_ts"],
    )


def _view(row: dict[str, Any]) -> dict[str, Any]:
    request = _request_of(row)
    expired = request.is_expired()
    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "action_payload": json.loads(row["action_payload"] or "{}"),
        "action_payload_hash": row["action_payload_hash"],
        "risk_level": row["risk_level"],
        "requires_human": requires_human(RiskLevel(row["risk_level"])),
        "human_summary": row["human_summary"],
        "reversibility": row["reversibility"],
        "requested_by": row["requested_by"],
        "requested_ts": row["requested_ts"],
        "expires_at": row["expires_at"],
        "state": "expired" if (expired and row["state"] == "pending") else row["state"],
        "granted_by": row["granted_by"],
        "granted_ts": row["granted_ts"],
        "consumed_ts": row["consumed_ts"],
        "reason": row["decided_reason"],
        "risk_floor": floor_for(row["action_type"]).value,
    }


def _publish(
    request: Request,
    kind: str,
    payload: dict[str, Any],
    *,
    approval_id: str,
    task_id: str | None = None,
    mission_id: str | None = None,
) -> None:
    request.app.state.bus.publish(
        kind,
        {"approval_id": approval_id, **payload},
        method="measured",
        task_id=task_id,
        mission_id=mission_id,
    )


# --------------------------------------------------------------------------------- endpoints


@router.get("/v1/approvals")
def list_approvals(request: Request, state: str | None = None) -> dict[str, Any]:
    """The inbox: pending first, then whatever else the caller asked for."""
    store = _store(request)
    rows = store.rows("SELECT * FROM approvals ORDER BY requested_ts DESC")
    views = [_view(row) for row in rows]
    if state and state != "all":
        views = [view for view in views if view["state"] == state]
    return {
        "approvals": views,
        "pending": [view["id"] for view in views if view["state"] == "pending"],
        "counts": {
            name: sum(1 for view in views if view["state"] == name)
            for name in sorted({view["state"] for view in views})
        },
    }


@router.get("/v1/approvals/{approval_id}")
def get_approval(approval_id: str, request: Request) -> dict[str, Any]:
    return _view(_row_or_404(_store(request), approval_id))


@router.post("/v1/approvals")
def request_approval(payload: ApprovalIn, request: Request) -> dict[str, Any]:
    """Ask for authorisation for one exact action. This authorises nothing by itself."""
    store = _store(request)
    try:
        risk = RiskLevel(payload.risk_level)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown risk level: {payload.risk_level}") from None
    if "." not in payload.action_type:
        raise HTTPException(
            status_code=422, detail=f"action_type must be dotted (verb.noun), got {payload.action_type!r}"
        )
    floor = floor_for(payload.action_type)
    if _ORDER[risk] < _ORDER[floor]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{payload.action_type} may not be proposed at {risk.value}: its declared floor is "
                f"{floor.value}. A runtime may propose a higher risk, never a lower one."
            ),
        )
    if not payload.requested_by.strip():
        raise HTTPException(status_code=422, detail="an approval request must name who is asking")
    if requires_human(risk) and not payload.human_summary.strip():
        raise HTTPException(
            status_code=422, detail=f"{risk.value} requires a human summary of what is being authorised"
        )

    # The scope is checked against the log rather than taken on trust: a task must exist, and a
    # mission named alongside it must be the mission that task actually belongs to.
    mission_id = payload.mission_id
    if payload.task_id:
        task_rows = store.rows("SELECT id, mission_id FROM tasks WHERE id = ?", (payload.task_id,))
        if not task_rows:
            raise HTTPException(status_code=404, detail=f"no such task: {payload.task_id}")
        if mission_id and mission_id != task_rows[0]["mission_id"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"task {payload.task_id} belongs to mission {task_rows[0]['mission_id']}, "
                    f"not {mission_id}"
                ),
            )
        mission_id = task_rows[0]["mission_id"]

    approval_id = new_id(IdKind.APPROVAL)
    ttl = payload.ttl_s if payload.ttl_s is not None else APPROVAL_TTL_S
    model = ApprovalRequest(
        id=approval_id,
        risk_level=risk,
        action_type=payload.action_type,
        action_payload_hash=payload_hash(payload.action_type, payload.action_payload),
        human_summary=payload.human_summary,
        reversibility=payload.reversibility,
        expires_at=time.time() + ttl,
        requested_by=payload.requested_by,
        action_payload=payload.action_payload,
    )
    _publish(
        request,
        "approval.requested",
        {
            "id": model.id,
            "action_type": model.action_type,
            "action_payload": model.action_payload,
            "action_payload_hash": model.action_payload_hash,
            "risk_level": model.risk_level.value,
            "human_summary": model.human_summary,
            "reversibility": model.reversibility,
            "requested_by": model.requested_by,
            "expires_at": model.expires_at,
        },
        approval_id=approval_id,
            task_id=payload.task_id,
            mission_id=mission_id,
    )
    return _view(_row_or_404(store, approval_id))


@router.post("/v1/approvals/{approval_id}/grant")
def grant_approval(approval_id: str, payload: GrantIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    row = _row_or_404(store, approval_id)
    if row["state"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"approval {approval_id} is {row['state']} and cannot be granted"
        )
    model = _request_of(row)
    if model.is_expired():
        _publish(request, "approval.expired", {"reason": "ttl passed before a decision"}, approval_id=approval_id)
        raise HTTPException(status_code=409, detail=f"approval {approval_id} expired before it was granted")
    risk = RiskLevel(row["risk_level"])
    # Authority is a kind, not a name. Before S2 this asked only that a non-empty string arrived in
    # `by`; a runtime could therefore approve its own R3/R4 action by typing a person's name. The
    # decision now reads the authenticated actor, and `by` stays what it always was: a label.
    if grant_requires_operator(risk.value, actor_from_request(request)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{risk.value} requires a named human approver; an approval without one is a "
                "delegation, and R4 can never be delegated (BOOK §40/§44)"
            ),
        )
    try:
        granted = model.grant(by=payload.by or row["requested_by"])
    except Exception as exc:  # the contract refuses; the API reports the refusal
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _publish(
        request,
        "approval.granted",
        {"by": granted.granted_by, "granted_at": granted.granted_at},
        approval_id=approval_id,
    )
    return _view(_row_or_404(store, approval_id))


@router.post("/v1/approvals/{approval_id}/deny")
def deny_approval(approval_id: str, payload: DenyIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    row = _row_or_404(store, approval_id)
    if row["state"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"approval {approval_id} is {row['state']} and cannot be denied"
        )
    _publish(
        request,
        "approval.denied",
        {"by": payload.by, "reason": payload.reason},
        approval_id=approval_id,
    )
    return _view(_row_or_404(store, approval_id))


@router.post("/v1/approvals/{approval_id}/expire")
def expire_approval(approval_id: str, request: Request) -> dict[str, Any]:
    """Record that the window closed. Expiry is a fact in the log, not a client-side clock."""
    store = _store(request)
    row = _row_or_404(store, approval_id)
    model = _request_of(row)
    if row["state"] == "pending" and model.is_expired():
        _publish(request, "approval.expired", {"reason": "ttl passed"}, approval_id=approval_id)
    return _view(_row_or_404(store, approval_id))


@router.post("/v1/approvals/{approval_id}/consume")
def consume_approval(approval_id: str, payload: ConsumeIn, request: Request) -> dict[str, Any]:
    """Execute, if -- and only if -- this approval covers exactly this action.

    This is the moment the binding matters: the hash of what is about to happen is compared with
    the hash of what a human saw.
    """
    store = _store(request)
    row = _row_or_404(store, approval_id)
    if row["state"] == "consumed":
        raise HTTPException(
            status_code=409,
            detail=f"approval {approval_id} was already consumed at {row['consumed_ts']}; an approval is not reusable",
        )
    if row["state"] == "denied":
        raise HTTPException(status_code=409, detail=f"approval {approval_id} was denied and authorises nothing")
    if row["state"] == "expired":
        raise HTTPException(status_code=409, detail=f"approval {approval_id} expired and authorises nothing")
    model = _request_of(row)
    if not model.granted:
        raise HTTPException(
            status_code=409, detail=f"approval {approval_id} was never granted; asking is not authorisation"
        )
    if model.is_expired():
        _publish(request, "approval.expired", {"reason": "ttl passed before consumption"}, approval_id=approval_id)
        raise HTTPException(status_code=409, detail=f"approval {approval_id} expired before it was used")
    expected = payload_hash(payload.action_type, payload.action_payload)
    if not model.authorises(payload.action_type, payload.action_payload):
        raise HTTPException(
            status_code=409,
            detail=(
                f"approval {approval_id} does not cover this action: it authorised "
                f"{model.action_type} at {model.action_payload_hash}, this is {payload.action_type} at "
                f"{expected}. A different payload is a different authorisation."
            ),
        )
    _publish(
        request,
        "approval.consumed",
        {"by": payload.by, "action_type": payload.action_type, "action_payload_hash": expected},
        approval_id=approval_id,
    )
    return {"authorised": True, "approval_id": approval_id, "action_payload_hash": expected, "approval": _view(_row_or_404(store, approval_id))}
