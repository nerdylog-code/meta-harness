#!/usr/bin/env python3
"""Generate the JSON Schema and the TypeScript mirror from the Python contracts.

    python packages/contracts/scripts/generate.py            # write the artifacts
    python packages/contracts/scripts/generate.py --check     # fail if they drift

Why a generator instead of two hand-written definitions (WP-003 decision 13):
two manual copies of the same interface drift, and the drift is discovered in
production. Python is the source of truth here; the schema and the TypeScript
file are *derived*, and `tests/contracts/test_parity.py` regenerates them and
compares bytes, so a hand edit to the generated files is a failing test rather
than a silent divergence.

The TypeScript emitter covers exactly the JSON Schema constructs this contract
set uses and raises on anything else, so it cannot quietly emit a wrong type for
a construct it does not understand.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
CONTRACTS_DIR = HERE.parent.parent
REPO_ROOT = CONTRACTS_DIR.parent.parent
if str(CONTRACTS_DIR) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_DIR))

SCHEMA_PATH = CONTRACTS_DIR / "schema" / "contracts.schema.json"
TS_PATH = CONTRACTS_DIR / "ts" / "contracts.ts"

HEADER_TS = """// GENERATED FILE — DO NOT EDIT BY HAND.
//
// Source of truth: packages/contracts/metaharness_contracts/*.py
// Regenerate:      python packages/contracts/scripts/generate.py
// Parity is enforced by tests/contracts/test_parity.py (byte comparison).
//
// TypeScript has no runtime validation here on purpose: these are the *types*
// the UI consumes, and the validation lives on the Python side where the data
// enters the system.

"""

_UNSUPPORTED = ("oneOf", "allOf", "not", "if", "then", "else", "patternProperties", "prefixItems")


def build_schema() -> dict[str, Any]:
    from metaharness_contracts import WIRE_CONTRACTS, contract_models

    models = contract_models()
    definitions: dict[str, Any] = {}
    for name in WIRE_CONTRACTS:
        model = models[name]
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        nested = schema.pop("$defs", {})
        for key, value in nested.items():
            definitions.setdefault(key, value)
        # The model itself lives at $defs/<name> so every $ref is resolvable
        # from one flat namespace.
        schema.pop("title", None)
        definitions[name] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://meta-harness.local/contracts/contracts.schema.json",
        "title": "Meta-Harness v2 contracts",
        "x-contract-version": 1,
        "x-contracts": list(WIRE_CONTRACTS),
        "$defs": {key: definitions[key] for key in sorted(definitions)},
    }


def reference_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def ts_type(node: Any, *, definitions: dict[str, Any], inline: bool = False) -> str:
    if not isinstance(node, dict):
        raise ValueError(f"schema node is not an object: {node!r}")
    for key in _UNSUPPORTED:
        if key in node:
            raise ValueError(f"the TypeScript emitter does not handle {key!r}: {node}")

    if "$ref" in node:
        return reference_name(node["$ref"])

    if "enum" in node:
        values = " | ".join(json.dumps(value) for value in node["enum"])
        return f"({values})" if inline else values

    if "anyOf" in node:
        parts: list[str] = []
        nullable = False
        for option in node["anyOf"]:
            rendered = ts_type(option, definitions=definitions)
            if rendered == "null":
                nullable = True
                continue
            if rendered not in parts:
                parts.append(rendered)
        if not parts:
            parts = ["unknown"]
        base = " | ".join(sorted(parts))
        return f"{base} | null" if nullable else base

    node_type = node.get("type")
    if isinstance(node_type, list):
        nullable = "null" in node_type
        types = [value for value in node_type if value != "null"]
        if not types:
            return "null"
        rendered = " | ".join(sorted(ts_type({**node, "type": value}, definitions=definitions) for value in types))
        return f"{rendered} | null" if nullable else rendered

    if node_type == "string":
        return "string"
    if node_type in {"number", "integer"}:
        return "number"
    if node_type == "boolean":
        return "boolean"
    if node_type == "null":
        return "null"
    if node_type == "array":
        items = node.get("items")
        inner = ts_type(items, definitions=definitions) if items else "unknown"
        return f"{inner}[]"
    if node_type == "object" or "properties" in node:
        properties = node.get("properties") or {}
        if properties:
            body = "; ".join(
                f"{quote_key(name)}: {ts_type(value, definitions=definitions)}"
                for name, value in properties.items()
            )
            return "{ " + body + " }"
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {ts_type(extra, definitions=definitions)}>"
        if extra is True:
            return "Record<string, unknown>"
        return "Record<string, unknown>"
    if not node:
        return "unknown"
    raise ValueError(f"unsupported schema node: {json.dumps(node, sort_keys=True)}")


def quote_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else json.dumps(name)


def emit_ts(schema: dict[str, Any]) -> str:
    definitions: dict[str, Any] = schema["$defs"]
    lines: list[str] = [HEADER_TS.rstrip("\n"), "", f"export const CONTRACT_VERSION = {schema['x-contract-version']};", ""]

    enums = sorted(name for name, node in definitions.items() if "enum" in node)
    for name in enums:
        node = definitions[name]
        lines.append(f"export type {name} = {ts_type(node, definitions=definitions)};")
    if enums:
        lines.append("")

    for name in sorted(definitions):
        node = definitions[name]
        if "enum" in node:
            continue
        description = node.get("description")
        if description:
            lines.append("/** " + " ".join(str(description).split()) + " */")
        if node.get("type") == "object" or "properties" in node:
            required = set(node.get("required") or [])
            properties: dict[str, Any] = node.get("properties") or {}
            lines.append(f"export interface {name} {{")
            for prop in sorted(properties):
                prop_node = properties[prop]
                prop_description = prop_node.get("description")
                if prop_description:
                    lines.append("  /** " + " ".join(str(prop_description).split()) + " */")
                optional = "" if prop in required else "?"
                lines.append(f"  {quote_key(prop)}{optional}: {ts_type(prop_node, definitions=definitions)};")
            extra = node.get("additionalProperties")
            if isinstance(extra, dict):
                lines.append(f"  [key: string]: {ts_type(extra, definitions=definitions)};")
            elif extra is True:
                lines.append("  [key: string]: unknown;")
            lines.append("}")
        else:
            lines.append(f"export type {name} = {ts_type(node, definitions=definitions)};")
        lines.append("")

    lines.append("/** The union of every wire contract, for discriminated handling in the UI. */")
    lines.append("export type WireContract =")
    for name in schema["x-contracts"]:
        lines.append(f"  | {name}")
    lines[-1] = lines[-1] + ";"
    lines.append("")
    return "\n".join(lines)


def emit_schema(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_artifacts(*, check: bool) -> int:
    schema = build_schema()
    schema_text = emit_schema(schema)
    ts_text = emit_ts(schema)

    targets = ((SCHEMA_PATH, schema_text), (TS_PATH, ts_text))
    stale: list[str] = []
    for path, text in targets:
        if check:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            if current != text:
                stale.append(str(path.relative_to(REPO_ROOT)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[generate] wrote {path.relative_to(REPO_ROOT)} ({len(text)} bytes)")

    if check and stale:
        print("[generate] generated artifacts are stale:")
        for name in stale:
            print(f"  - {name}")
        print("[generate] run: python packages/contracts/scripts/generate.py")
        return 1
    if check:
        print("[generate] artifacts are up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate contract artifacts from the Python models.")
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args(argv)
    return write_artifacts(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
