"""Usage and provenance — the gate item 'unknown != zero'."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "packages" / "contracts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "contracts"))

import metaharness_contracts as mc  # noqa: E402


class TestMetric(unittest.TestCase):
    def test_unknown_metric_has_no_number(self) -> None:
        metric = mc.Metric.unknown(unit="tokens")
        self.assertFalse(metric.known)
        self.assertIsNone(metric.value)
        self.assertIs(metric.provenance, mc.Provenance.UNKNOWN)

    def test_unknown_cannot_carry_a_number(self) -> None:
        with self.assertRaises(ValueError):
            mc.Metric(value=0, provenance=mc.Provenance.UNKNOWN)
        with self.assertRaises(ValueError):
            mc.Metric(value=17.0, provenance=mc.Provenance.UNKNOWN)

    def test_a_claim_of_measurement_needs_a_value(self) -> None:
        for provenance in (mc.Provenance.MEASURED, mc.Provenance.PROVIDER_REPORTED, mc.Provenance.ESTIMATED):
            with self.assertRaises(ValueError, msg=provenance.value):
                mc.Metric(value=None, provenance=provenance)

    def test_helpers_label_their_provenance(self) -> None:
        self.assertIs(mc.Metric.measured(10).provenance, mc.Provenance.MEASURED)
        self.assertIs(mc.Metric.reported(10).provenance, mc.Provenance.PROVIDER_REPORTED)
        self.assertIs(
            mc.Metric.reported(10, provenance=mc.Provenance.ESTIMATED).provenance, mc.Provenance.ESTIMATED
        )
        with self.assertRaises(ValueError):
            mc.Metric.reported(10, provenance=mc.Provenance.UNKNOWN)

    def test_zero_must_be_asked_for_explicitly(self) -> None:
        """The whole point: a missing measurement must not read as 0."""
        missing = mc.Metric.unknown(unit="tokens")
        self.assertIsNone(missing.value)
        self.assertEqual(missing.as_number(), 0.0)  # only when the caller says so
        self.assertEqual(missing.as_number(default=42.0), 42.0)

    def test_metrics_are_immutable(self) -> None:
        metric = mc.Metric.measured(1)
        with self.assertRaises(Exception):
            metric.value = 2  # type: ignore[misc]


class TestUsageSample(unittest.TestCase):
    def sample(self) -> mc.UsageSample:
        return mc.UsageSample(
            input_tokens=mc.Metric.reported(1200, provenance=mc.Provenance.PROVIDER_REPORTED),
            output_tokens=mc.Metric.reported(340, provenance=mc.Provenance.PROVIDER_REPORTED),
            cache_read_tokens=mc.Metric.reported(800, provenance=mc.Provenance.PROVIDER_REPORTED),
            duration_ms=mc.Metric.measured(1234.5),
            runtime="fake",
            model="fake-model-1",
        )

    def test_default_sample_is_entirely_unknown(self) -> None:
        sample = mc.UsageSample()
        self.assertEqual(set(sample.unknown_fields), set(mc.UsageSample.METRIC_FIELDS))
        for name in mc.UsageSample.METRIC_FIELDS:
            self.assertIsNone(getattr(sample, name).value, name)

    def test_provenance_is_per_metric_not_per_sample(self) -> None:
        sample = self.sample()
        self.assertIs(sample.input_tokens.provenance, mc.Provenance.PROVIDER_REPORTED)
        self.assertIs(sample.duration_ms.provenance, mc.Provenance.MEASURED)
        self.assertIs(sample.provider_cost.provenance, mc.Provenance.UNKNOWN)
        self.assertIn("provider_cost", sample.unknown_fields)
        self.assertNotIn("input_tokens", sample.unknown_fields)
        # 19 metric fields, 4 known in this sample
        self.assertEqual(len(sample.unknown_fields), len(mc.UsageSample.METRIC_FIELDS) - 4)

    def test_unknown_is_never_zero_after_round_trip(self) -> None:
        encoded = mc.dumps(self.sample())
        reparsed = mc.UsageSample.model_validate_json(encoded)
        self.assertIsNone(reparsed.provider_cost.value)
        self.assertIs(reparsed.provider_cost.provenance, mc.Provenance.UNKNOWN)
        self.assertEqual(reparsed.model_dump(), self.sample().model_dump())

    def test_cache_hit_ratio_needs_real_numbers(self) -> None:
        ratio = self.sample().cache_hit_ratio
        self.assertAlmostEqual(ratio.value or 0.0, 0.6667, places=3)
        self.assertIs(ratio.provenance, mc.Provenance.RUNTIME_REPORTED)
        # unknown inputs must not produce a fabricated ratio
        blank = mc.UsageSample().cache_hit_ratio
        self.assertFalse(blank.known)
        self.assertIsNone(blank.value)

    def test_estimated_inputs_never_produce_a_measured_ratio(self) -> None:
        sample = mc.UsageSample(
            input_tokens=mc.Metric.reported(100, provenance=mc.Provenance.ESTIMATED),
            cache_read_tokens=mc.Metric.reported(50, provenance=mc.Provenance.PROVIDER_REPORTED),
        )
        self.assertIs(sample.cache_hit_ratio.provenance, mc.Provenance.ESTIMATED)

    def test_merge_prefers_the_stronger_provenance_per_metric(self) -> None:
        measured = mc.UsageSample(input_tokens=mc.Metric.measured(100))
        reported = mc.UsageSample(
            input_tokens=mc.Metric.reported(999, provenance=mc.Provenance.PROVIDER_REPORTED),
            output_tokens=mc.Metric.reported(7, provenance=mc.Provenance.PROVIDER_REPORTED),
        )
        merged = measured.merge(reported)
        self.assertEqual(merged.input_tokens.value, 100.0)
        self.assertIs(merged.input_tokens.provenance, mc.Provenance.MEASURED)
        self.assertEqual(merged.output_tokens.value, 7.0)
        self.assertIs(merged.output_tokens.provenance, mc.Provenance.PROVIDER_REPORTED)

    def test_usage_model_is_fail_closed_on_unknown_fields(self) -> None:
        with self.assertRaises(Exception):
            mc.UsageSample.model_validate({"input_tokens": 12, "surprise": True})


if __name__ == "__main__":
    unittest.main()
