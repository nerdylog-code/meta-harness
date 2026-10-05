"""Contract hygiene: no vendor coupling, no OS-conditional code, no hidden I/O.

Three of the WP-003 gate items are negative properties, and a negative property
only means something if it is checked continuously:

* `CanonicalEvent` / `CapabilitySet` must not depend on Pi, Hermes, OpenClaw or
  OMP — a runtime is a string, never a branch in the schema.
* Windows and Linux must not diverge semantically — the contracts contain no OS
  branch, no path arithmetic and no platform import.
* The contract layer must not reach for the network, the filesystem or a
  subprocess. It is a specification, not a component.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "packages" / "contracts"
PKG_DIR = CONTRACTS_DIR / "metaharness_contracts"
TS_PATH = CONTRACTS_DIR / "ts" / "contracts.ts"
if str(CONTRACTS_DIR) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_DIR))

import metaharness_contracts as mc  # noqa: E402

VENDOR_TOKENS = ("pi", "hermes", "openclaw", "omp", "deepseek", "anthropic", "openai")
FORBIDDEN_IMPORTS = (
    "os",
    "sys",
    "platform",
    "subprocess",
    "socket",
    "httpx",
    "requests",
    "urllib",
    "urllib3",
)
#: ``pathlib`` is deliberately allowed as a *type*: ArtifactRef rejects Path
#: objects that would not survive JSON. What is forbidden is touching a file, so
#: the call-level check below is the one that matters.
FORBIDDEN_CALLS = ("open(", ".read_text(", ".write_text(", ".read_bytes(", "os.system", "subprocess.run")
#: Modules that may appear in a serialized contract (checked case-insensitively).
VENDOR_FREE_CONTRACTS = ("CanonicalEvent", "CapabilitySet", "UsageSample", "SessionSpec", "RuntimeInfo")


class TestVendorCoupling(unittest.TestCase):
    def test_wire_contract_schemas_mention_no_vendor(self) -> None:
        models = mc.contract_models()
        for name in VENDOR_FREE_CONTRACTS:
            schema = json.dumps(models[name].model_json_schema(), sort_keys=True).lower()
            for token in ("hermes", "openclaw", "deepseek", "anthropic", "openai"):
                self.assertNotIn(token, schema, f"{name} schema mentions {token!r}")
            # "pi" is too short to search safely (it appears inside words); the
            # field-level check below is what catches a vendor-shaped field.
            self.assertNotIn("runtime_id\": {\"const\"", schema)

    def test_no_vendor_shaped_field_in_the_core_contracts(self) -> None:
        models = mc.contract_models()
        for name in VENDOR_FREE_CONTRACTS:
            fields = set(models[name].model_fields)
            for token in VENDOR_TOKENS:
                offenders = {field for field in fields if field.lower() in {token, f"{token}_id"} or field.lower().startswith(f"{token}_")}
                self.assertEqual(offenders, set(), f"{name} has vendor-shaped fields: {offenders}")

    def test_generated_typescript_is_vendor_free_outside_comments(self) -> None:
        text = TS_PATH.read_text(encoding="utf-8")
        code_lines = [
            line
            for line in text.splitlines()
            if not line.strip().startswith("//") and not line.strip().startswith("/*") and not line.strip().startswith("*")
        ]
        lowered = "\n".join(code_lines).lower()
        for token in ("hermes", "openclaw", "deepseek", "anthropic", "openai"):
            self.assertNotIn(token, lowered, f"generated TypeScript mentions {token!r}")

    def test_contracts_never_touch_a_file_or_the_network(self) -> None:
        """The contract layer is a specification: no I/O, not even to read a fixture."""
        offenders: list[str] = []
        for path in sorted(PKG_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for needle in FORBIDDEN_CALLS:
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [], f"contracts must stay IO-free: {offenders}")


class TestNoOSConditionalCode(unittest.TestCase):
    def test_contracts_import_no_platform_or_io_module(self) -> None:
        offenders: list[str] = []
        for path in sorted(PKG_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    root = name.split(".")[0]
                    if root in FORBIDDEN_IMPORTS:
                        offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [], f"contracts must stay platform- and IO-free: {offenders}")

    def test_contracts_do_not_import_the_daemon(self) -> None:
        offenders: list[str] = []
        for path in sorted(PKG_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(alias.name == "metaharness" for alias in node.names):
                    offenders.append(path.name)
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("metaharness"):
                    offenders.append(path.name)
        self.assertEqual(offenders, [], "the contract layer must not depend on the daemon")

    def test_artifact_paths_are_normalized_the_same_on_both_platforms(self) -> None:
        windows = mc.ArtifactRef(
            id=mc.new_id(mc.IdKind.ARTIFACT),
            path="artifacts\\logs\\a.txt",
            sha256="a" * 64,
            mime="text/plain",
            size=1,
        )
        posix = mc.ArtifactRef(
            id=mc.new_id(mc.IdKind.ARTIFACT),
            path="artifacts/logs/a.txt",
            sha256="a" * 64,
            mime="text/plain",
            size=1,
        )
        self.assertEqual(windows.path, posix.path)
        # Serialized without the (unique) id, the two records are identical, so
        # a Windows-produced artifact and a Linux-produced one are the same
        # logical object: no semantic divergence between the platforms.
        self.assertEqual(
            mc.dumps(windows.model_dump(exclude={"id"})), mc.dumps(posix.model_dump(exclude={"id"}))
        )

    def test_no_os_module_is_used_even_indirectly(self) -> None:
        """`os.name` / `sys.platform` must not appear anywhere in the package."""
        for path in sorted(PKG_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for needle in ("os.name", "sys.platform", "platform.system", "os.sep", "os.path"):
                self.assertNotIn(needle, text, f"{path.name} uses {needle}")


class TestSpecIsSelfContained(unittest.TestCase):
    def test_every_wire_contract_exists_and_is_exported(self) -> None:
        models = mc.contract_models()
        self.assertEqual(set(models), set(mc.WIRE_CONTRACTS))
        for name in mc.WIRE_CONTRACTS:
            self.assertTrue(hasattr(mc, name), f"{name} is not exported")

    def test_envelopes_and_capabilities_are_vendor_neutral_by_construction(self) -> None:
        """A runtime id is data: the same envelope shape carries any runtime."""
        event = mc.CanonicalEvent.build(
            "runtime.probe",
            {"v": 1, "available": True},
            event_id=mc.new_id(mc.IdKind.EVENT),
            seq=1,
            runtime_id="rt_any00000000000000",
        )
        self.assertEqual(event.runtime_id, "rt_any00000000000000")
        self.assertNotIn("runtime", set(mc.CanonicalEvent.model_fields) - {"runtime_id"})


if __name__ == "__main__":
    unittest.main()
