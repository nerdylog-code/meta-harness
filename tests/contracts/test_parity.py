"""Python/TypeScript parity.

WP-003 decision 13: one logical specification. Python is the source of truth and
the TypeScript mirror is generated; this test fails if the committed artifacts no
longer match what the generator produces, so a hand edit to `contracts.ts` is a
failing test rather than a divergence nobody notices until the UI misbehaves.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "packages" / "contracts"
GENERATOR = CONTRACTS_DIR / "scripts" / "generate.py"
SCHEMA_PATH = CONTRACTS_DIR / "schema" / "contracts.schema.json"
TS_PATH = CONTRACTS_DIR / "ts" / "contracts.ts"
if str(CONTRACTS_DIR) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_DIR))

import metaharness_contracts as mc  # noqa: E402

TSC_CANDIDATES = (
    REPO_ROOT / "apps" / "web" / "node_modules" / ".bin" / "tsc",
    REPO_ROOT / "apps" / "web" / "node_modules" / ".bin" / "tsc.cmd",
)


def ts_interfaces(text: str) -> dict[str, str]:
    """Map interface/type name -> body, so field parity can be checked."""
    out: dict[str, str] = {}
    for match in re.finditer(r"^export (?:interface|type) (\w+)[^{;=]*", text, flags=re.MULTILINE):
        name = match.group(1)
        start = match.end()
        end = text.find("}", start) if text[start - 1 : start] == "{" or "{" in text[start : start + 1] else -1
        block = text[start:end] if end > 0 else text[start : text.find(";", start)]
        out[name] = block
    return out


class TestGeneratorParity(unittest.TestCase):
    def test_generated_artifacts_are_up_to_date(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            "generated contract artifacts are stale — run: python packages/contracts/scripts/generate.py\n"
            + result.stdout
            + result.stderr,
        )

    def test_schema_and_typescript_exist(self) -> None:
        self.assertTrue(SCHEMA_PATH.is_file(), "contracts.schema.json is missing")
        self.assertTrue(TS_PATH.is_file(), "contracts.ts is missing")
        self.assertGreater(SCHEMA_PATH.stat().st_size, 1000)
        self.assertGreater(TS_PATH.stat().st_size, 1000)

    def test_every_wire_contract_appears_in_both_artifacts(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        text = TS_PATH.read_text(encoding="utf-8")
        for name in mc.WIRE_CONTRACTS:
            self.assertIn(name, schema["$defs"], f"{name} missing from the schema")
            self.assertRegex(text, rf"(?m)^export (interface|type) {name}\b", f"{name} missing from the TypeScript")


class TestFieldParity(unittest.TestCase):
    def test_every_python_field_exists_in_the_typescript(self) -> None:
        text = TS_PATH.read_text(encoding="utf-8")
        blocks = ts_interfaces(text)
        problems: list[str] = []
        for name, model in mc.contract_models().items():
            block = blocks.get(name)
            if block is None:
                problems.append(f"{name}: no TypeScript declaration")
                continue
            for field in model.model_fields:
                if not re.search(rf"^\s*{re.escape(field)}\??\s*:", block, flags=re.MULTILINE):
                    problems.append(f"{name}.{field} missing in TypeScript")
        self.assertEqual(problems, [], "; ".join(problems))

    def test_schema_defs_and_typescript_declarations_agree_on_count(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        text = TS_PATH.read_text(encoding="utf-8")
        declarations = set(ts_interfaces(text))
        expected = set(schema["$defs"])
        self.assertEqual(
            expected - declarations,
            set(),
            f"declared in the schema but not in TypeScript: {sorted(expected - declarations)}",
        )

    def test_contract_version_is_present_in_both(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        text = TS_PATH.read_text(encoding="utf-8")
        self.assertEqual(schema["x-contract-version"], 1)
        self.assertIn(f"CONTRACT_VERSION = {schema['x-contract-version']}", text)


class TestTypeScriptCompiles(unittest.TestCase):
    def test_tsc_accepts_the_generated_file(self) -> None:
        tsc = next((candidate for candidate in TSC_CANDIDATES if candidate.is_file()), None)
        if tsc is None:
            if shutil.which("tsc") is None:
                self.skipTest("no local tsc; run `pnpm install` in apps/web to enable this check")
            tsc = Path(shutil.which("tsc") or "")
        result = subprocess.run(
            [str(tsc), "-p", str(CONTRACTS_DIR / "ts" / "tsconfig.json")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestGeneratorRefusesUnknownConstructs(unittest.TestCase):
    def test_unsupported_json_schema_features_raise(self) -> None:
        sys.path.insert(0, str(CONTRACTS_DIR / "scripts"))
        import generate  # noqa: PLC0415

        for construct in ("oneOf", "allOf", "patternProperties"):
            with self.assertRaises(ValueError, msg=construct):
                generate.ts_type({construct: []}, definitions={})
        self.assertEqual(generate.ts_type({"type": "string"}, definitions={}), "string")
        self.assertEqual(generate.ts_type({"type": "array", "items": {"type": "number"}}, definitions={}), "number[]")
        self.assertEqual(
            generate.ts_type({"anyOf": [{"type": "string"}, {"type": "null"}]}, definitions={}),
            "string | null",
        )


if __name__ == "__main__":
    unittest.main()
