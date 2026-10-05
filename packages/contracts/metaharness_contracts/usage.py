"""Per-metric provenance and the universal usage sample.

WP-003 decision 5: provenance is attached to **each metric**, not to the sample
as a whole, and `unknown` is never turned into `0`. The difference matters: a
benchmark that compares tokens/cost per accepted result (BOOK §51) is worthless
if a missing measurement silently becomes a zero.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import Provenance


class Metric(BaseModel):
    """A single measurement with the provenance of *that* value.

    ``value`` is None exactly when the metric is unknown — never 0.

    The type is intentionally **not** parametrized by int/float: JSON has one
    number type, so a `Metric[int]` in Python would promise a distinction the
    wire cannot carry and the TypeScript mirror could not express. Count fields
    (tokens, retries) are documented as counts and validated as such by their
    producers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float | None = None
    provenance: Provenance = Provenance.UNKNOWN
    unit: str | None = None
    source: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "Metric":
        if self.provenance is Provenance.UNKNOWN and self.value is not None:
            raise ValueError("provenance 'unknown' must carry value=None, not a number")
        if self.provenance is not Provenance.UNKNOWN and self.value is None:
            raise ValueError(
                f"provenance {self.provenance.value!r} claims a measurement but value is None"
            )
        return self

    @classmethod
    def unknown(cls, *, unit: str | None = None, note: str | None = None) -> "Metric":
        return cls(value=None, provenance=Provenance.UNKNOWN, unit=unit, note=note)

    @classmethod
    def measured(cls, value: float, **kwargs: Any) -> "Metric":
        return cls(value=float(value), provenance=Provenance.MEASURED, **kwargs)

    @classmethod
    def reported(
        cls,
        value: float,
        *,
        provenance: Provenance = Provenance.PROVIDER_REPORTED,
        **kwargs: Any,
    ) -> "Metric":
        if provenance is Provenance.UNKNOWN:
            raise ValueError("use Metric.unknown() for unmeasured values")
        return cls(value=float(value), provenance=provenance, **kwargs)

    @property
    def known(self) -> bool:
        return self.value is not None

    def as_number(self, default: float = 0.0) -> float:
        """Explicit, greppable conversion. Callers who want 0 must ask for it."""
        return self.value if self.value is not None else default


class UsageSample(BaseModel):
    """The universal usage record (BOOK §17/§49).

    Every numeric field is a :class:`Metric`, so `estimated` can never be
    mistaken for `provider_reported` and a missing value can never read as zero.
    """

    model_config = ConfigDict(extra="forbid")

    # tokens
    input_tokens: Metric = Field(default_factory=Metric.unknown)
    output_tokens: Metric = Field(default_factory=Metric.unknown)
    reasoning_tokens: Metric = Field(default_factory=Metric.unknown)
    cache_read_tokens: Metric = Field(default_factory=Metric.unknown)
    cache_write_tokens: Metric = Field(default_factory=Metric.unknown)
    system_tokens: Metric = Field(default_factory=Metric.unknown)
    tool_schema_tokens: Metric = Field(default_factory=Metric.unknown)
    retrieval_tokens: Metric = Field(default_factory=Metric.unknown)
    tool_result_tokens: Metric = Field(default_factory=Metric.unknown)
    context_tokens: Metric = Field(default_factory=Metric.unknown)
    context_limit: Metric = Field(default_factory=Metric.unknown)

    # compaction
    compaction_input: Metric = Field(default_factory=Metric.unknown)
    compaction_output: Metric = Field(default_factory=Metric.unknown)

    # money
    provider_cost: Metric = Field(default_factory=Metric.unknown)
    estimated_cost: Metric = Field(default_factory=Metric.unknown)

    # latency
    ttft_ms: Metric = Field(default_factory=Metric.unknown)
    duration_ms: Metric = Field(default_factory=Metric.unknown)
    tool_duration_ms: Metric = Field(default_factory=Metric.unknown)

    # counts
    retries: Metric = Field(default_factory=Metric.unknown)

    # identity of the measurement (not metrics: plain values)
    provider: str | None = None
    model: str | None = None
    runtime: str | None = None
    agent: str | None = None
    session: str | None = None
    run: str | None = None
    task: str | None = None

    #: ClassVar on purpose: this is a constant about the model, not a field of
    #: it. Without ClassVar pydantic would publish it as a wire field.
    METRIC_FIELDS: ClassVar[tuple[str, ...]] = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "system_tokens",
        "tool_schema_tokens",
        "retrieval_tokens",
        "tool_result_tokens",
        "context_tokens",
        "context_limit",
        "compaction_input",
        "compaction_output",
        "provider_cost",
        "estimated_cost",
        "ttft_ms",
        "duration_ms",
        "tool_duration_ms",
        "retries",
    )

    def metrics(self) -> dict[str, Metric]:
        return {name: getattr(self, name) for name in self.METRIC_FIELDS}

    @property
    def unknown_fields(self) -> list[str]:
        return [name for name, metric in self.metrics().items() if not metric.known]

    @property
    def cache_hit_ratio(self) -> Metric[float]:
        """Read tokens served from cache, as a ratio of input tokens.

        Both inputs must be real measurements; otherwise the ratio is unknown —
        not zero, and not a guess built from an estimate.
        """
        read = self.cache_read_tokens
        total = self.input_tokens
        read_value = read.value
        total_value = total.value
        if read_value is None or total_value is None or total_value == 0:
            return Metric.unknown(unit="ratio")
        provenance = (
            Provenance.MEASURED
            if {read.provenance, total.provenance} == {Provenance.MEASURED}
            else Provenance.ESTIMATED
            if Provenance.ESTIMATED in {read.provenance, total.provenance}
            else Provenance.RUNTIME_REPORTED
        )
        return Metric(
            value=round(read_value / total_value, 4), provenance=provenance, unit="ratio"
        )

    def merge(self, other: "UsageSample") -> "UsageSample":
        """Combine two samples, preferring the stronger provenance per metric.

        Used when a runtime reports a partial sample and the kernel fills the
        gaps by measuring; the stronger label wins and nothing is averaged
        across different kinds of evidence.
        """
        order = {
            Provenance.MEASURED: 0,
            Provenance.PROVIDER_REPORTED: 1,
            Provenance.RUNTIME_REPORTED: 2,
            Provenance.ESTIMATED: 3,
            Provenance.UNKNOWN: 4,
        }
        merged: dict[str, Any] = {}
        for name in self.METRIC_FIELDS:
            mine = getattr(self, name)
            theirs = getattr(other, name)
            chosen = mine if order[mine.provenance] <= order[theirs.provenance] else theirs
            merged[name] = chosen
        identity = {
            key: getattr(self, key) or getattr(other, key)
            for key in ("provider", "model", "runtime", "agent", "session", "run", "task")
        }
        return UsageSample(**merged, **identity)
