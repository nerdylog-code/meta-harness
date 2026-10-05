"""Plugin manifest.

WP-003 decision 10. Note what is **absent**: trust. Trust is a property of the
system's assessment of a plugin, not a field the plugin writes about itself —
self-declared trust would be exactly the vulnerability the BOOK §10/§18 safety
floor exists to prevent. `tests/contracts/test_plugin.py` asserts the field is
not part of the manifest.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ContractError


class PluginKind(str, Enum):
    """BOOK §10."""

    RUNTIME_ADAPTER = "RuntimeAdapter"
    TOOL_PROVIDER = "ToolProvider"
    CONTEXT_ENGINE = "ContextEngine"
    MEMORY_PROVIDER = "MemoryProvider"
    RAG_PROVIDER = "RagProvider"
    SANDBOX_PROVIDER = "SandboxProvider"
    WORKSPACE_PROVIDER = "WorkspaceProvider"
    CHANNEL_PROVIDER = "ChannelProvider"
    VOICE_PROVIDER = "VoiceProvider"
    WORKFLOW_PROVIDER = "WorkflowProvider"
    RENDERER_PROVIDER = "RendererProvider"
    METRICS_EXPORTER = "MetricsExporter"
    SKILL_PROVIDER = "SkillProvider"
    MODEL_PROVIDER = "ModelProvider"


UNLOAD_SEMANTICS: tuple[str, ...] = ("hot", "restart-required", "drain-then-unload")


class PluginManifest(BaseModel):
    """The minimum a plugin must declare to be loadable."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    kind: PluginKind
    entrypoint: str
    provides: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    events_consumed: list[str] = Field(default_factory=list)
    events_produced: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    health_check: str | None = None
    model_visible_surfaces: list[str] = Field(default_factory=list)
    unload_semantics: str = "hot"

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not value or value != value.strip() or " " in value:
            raise ContractError(f"plugin id must be a bare token, got {value!r}")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not value or not value[0].isdigit():
            raise ContractError(f"plugin version must start with a digit, got {value!r}")
        return value

    @field_validator("unload_semantics")
    @classmethod
    def _validate_unload(cls, value: str) -> str:
        if value not in UNLOAD_SEMANTICS:
            raise ContractError(f"unload_semantics must be one of {UNLOAD_SEMANTICS}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _validate_events(self) -> "PluginManifest":
        for kind in [*self.events_consumed, *self.events_produced]:
            if "." not in kind:
                raise ContractError(f"event kinds must be namespaced, got {kind!r}")
        if self.kind is PluginKind.RUNTIME_ADAPTER and not self.provides:
            raise ContractError("a RuntimeAdapter must declare at least one provided capability")
        return self

    def declares(self, capability: str) -> bool:
        return capability in self.provides

    def needs(self, capability: str) -> bool:
        return capability in self.requires
