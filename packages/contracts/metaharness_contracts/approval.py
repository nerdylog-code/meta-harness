"""Approvals and action proposals.

WP-003 decisions 8 and 9. The important property is *binding*: an approval
authorises one exact action, identified by a hash of its payload. Re-using an
approval for a different payload must fail, and the failure must be detectable
locally (no LLM, no network, no judgement call).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ContractError, RiskLevel
from .ids import IdKind, is_valid_id

APPROVAL_TTL_S = 600.0


def payload_hash(action_type: str, action_payload: dict[str, Any]) -> str:
    """Stable hash of exactly what is being authorised.

    Canonical JSON (sorted keys, no NaN) means the hash does not depend on dict
    ordering or on the encoder's mood.
    """
    canonical = json.dumps(
        {"action_type": action_type, "payload": action_payload},
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ActionProposal(BaseModel):
    """A proposal. It authorises nothing; it is the input to an approval."""

    model_config = ConfigDict(extra="forbid")

    action: str
    impact: str
    risk: RiskLevel
    reversible: bool
    required_approval: bool
    evidence: list[str] = Field(default_factory=list)
    action_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action")
    @classmethod
    def _validate_action(cls, value: str) -> str:
        if not value or "." not in value:
            raise ContractError(f"action must be dotted (verb.noun), got {value!r}")
        return value

    @property
    def payload_hash(self) -> str:
        return payload_hash(self.action, self.action_payload)

    def to_request(self, *, approval_id: str, requested_by: str, ttl_s: float = APPROVAL_TTL_S) -> "ApprovalRequest":
        return ApprovalRequest(
            id=approval_id,
            risk_level=self.risk,
            action_type=self.action,
            action_payload_hash=self.payload_hash,
            human_summary=self.impact,
            reversibility="reversible" if self.reversible else "irreversible",
            requested_by=requested_by,
            expires_at=time.time() + ttl_s,
            action_payload=self.action_payload,
        )


class ApprovalRequest(BaseModel):
    """An authorisation bound to one payload hash."""

    model_config = ConfigDict(extra="forbid")

    id: str
    risk_level: RiskLevel
    action_type: str
    action_payload_hash: str
    human_summary: str
    reversibility: str
    expires_at: float | None = None
    requested_by: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
    granted: bool = False
    granted_by: str | None = None
    granted_at: float | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.APPROVAL):
            raise ContractError(f"approval id must be apr_-prefixed, got {value!r}")
        return value

    @field_validator("action_payload_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
            raise ContractError(f"action_payload_hash must be 'sha256:<64 hex>', got {value!r}")
        return value

    @model_validator(mode="after")
    def _validate_payload_binding(self) -> "ApprovalRequest":
        if self.action_payload:
            expected = payload_hash(self.action_type, self.action_payload)
            if expected != self.action_payload_hash:
                raise ContractError(
                    "action_payload_hash does not match action_payload: the approval would "
                    "authorise something other than what was reviewed"
                )
        return self

    # -- validity ----------------------------------------------------------
    def is_expired(self, *, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) > self.expires_at

    def grant(self, *, by: str, now: float | None = None) -> "ApprovalRequest":
        if self.is_expired(now=now):
            raise ContractError("refusing to grant an expired approval")
        if self.risk_level is RiskLevel.R4 and not by.strip():
            raise ContractError("R4 actions require a named human approver")
        return self.model_copy(
            update={"granted": True, "granted_by": by, "granted_at": now if now is not None else time.time()}
        )

    def authorises(self, action_type: str, action_payload: dict[str, Any], *, now: float | None = None) -> bool:
        """The single question that matters: does this cover *this* action?

        Returns False (never raises) for a mismatched payload, an ungranted or
        expired approval, or a different action type.
        """
        if not self.granted:
            return False
        if self.is_expired(now=now):
            return False
        if action_type != self.action_type:
            return False
        return payload_hash(action_type, action_payload) == self.action_payload_hash

    def require(self, action_type: str, action_payload: dict[str, Any], *, now: float | None = None) -> None:
        if not self.authorises(action_type, action_payload, now=now):
            raise ContractError(
                "approval does not cover this action: payload, action type, grant state or TTL differ"
            )


#: R4 may not be made auto-executable by any plugin (BOOK §40/§44).
NON_DELEGABLE_RISK: RiskLevel = RiskLevel.R4


def requires_human(risk: RiskLevel) -> bool:
    return risk in {RiskLevel.R3, RiskLevel.R4}
