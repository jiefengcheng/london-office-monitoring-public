"""Small validator for the checked-in contract's JSON Schema vocabulary.

Not a general-purpose JSON Schema implementation. No remote references, coercion,
defaults or schema supplied by a model are supported.
"""
from __future__ import annotations

from datetime import date
import math
import re


class ContractViolation(ValueError):
    pass


def validate(value, schema, document, path="value"):
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/$defs/"):
            raise ContractViolation("Only local contract definitions are supported")
        return validate(value, document["$defs"][reference[8:]], document, path)
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate(value, branch, document, path)
                matches += 1
            except ContractViolation:
                pass
        if matches != 1:
            raise ContractViolation(f"{path}: expected exactly one permitted shape")
        return
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    checks = {
        "object": lambda: isinstance(value, dict) and all(isinstance(k, str) for k in value),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
        "boolean": lambda: type(value) is bool,
        "null": lambda: value is None,
    }
    if kind is not None and not any(checks[k]() for k in kinds):
        raise ContractViolation(f"{path}: expected {kind}")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ContractViolation(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractViolation(f"{path}: unsupported value {value!r}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - value.keys()
        extra = value.keys() - properties.keys()
        if missing:
            raise ContractViolation(f"{path}: missing fields {sorted(missing)}")
        if extra and schema.get("additionalProperties") is False:
            raise ContractViolation(f"{path}: unexpected fields {sorted(extra)}")
        for key in sorted(value.keys() & properties.keys()):
            validate(value[key], properties[key], document, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ContractViolation(f"{path}: array length outside bounds")
        if "items" in schema:
            for index, item in enumerate(value):
                validate(item, schema["items"], document, f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise ContractViolation(f"{path}: text length outside bounds")
        if schema.get("minLength", 0) and not value.strip():
            raise ContractViolation(f"{path}: blank text is not permitted")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ContractViolation(f"{path}: invalid text format")
        if schema.get("format") == "date":
            try:
                valid_date = date.fromisoformat(value)
            except ValueError as error:
                raise ContractViolation(f"{path}: expected YYYY-MM-DD") from error
            if valid_date.isoformat() != value:
                raise ContractViolation(f"{path}: expected YYYY-MM-DD")
    elif type(value) in (int, float):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ContractViolation(f"{path}: number outside bounds")
