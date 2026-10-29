"""Provider-neutral decisions and the CP4 answer-selection protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Protocol

from contract_validation import validate

ROOT = Path(__file__).resolve().parent
ANSWER_SCHEMA = json.loads((ROOT / "contracts/agent-answer.schema.json").read_text(encoding="utf-8"))


class ProviderError(Exception):
    """A public code, never a raw HTTP body, credential or provider reasoning."""
    def __init__(self, code, diagnostic=None):
        super().__init__(code)
        self.code = code
        self.diagnostic = {}
        if isinstance(diagnostic, dict):
            if diagnostic.get("rule") in ("shape","constraints","series","scope"):
                self.diagnostic["rule"] = diagnostic["rule"]
            if diagnostic.get("task") in ("lookup","compare","brief","history","source","coverage","clarify","refuse","follow_up"):
                self.diagnostic["task"] = diagnostic["task"]
            for key in ("minimum","selected_count","allowed_count","unknown_count"):
                value=diagnostic.get(key)
                if type(value) is int and 0<=value<=200: self.diagnostic[key]=value
            refs=diagnostic.get("selected_supported_references")
            if isinstance(refs,list):
                import re
                self.diagnostic["selected_supported_references"] = [r for r in refs[:8] if isinstance(r,str) and re.fullmatch(r"(?:fact:F[0-9]{2,}|source:S[0-9]{2,}|(?:comparison|coverage):T[0-9]+\.O[0-9]+)",r)]


def strict_json(text, max_chars=100_000):
    if not isinstance(text, str) or len(text) > max_chars:
        raise ValueError("JSON text missing or over limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Nonfinite JSON constant")

    def floating(value):
        number = float(value)
        if not math.isfinite(number): raise ValueError("Nonfinite JSON number")
        return number

    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
    except RecursionError:
        raise ValueError("Excessively nested JSON") from None
    pending = [(result, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64: raise ValueError("Excessively nested JSON")
        if isinstance(item, dict): pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list): pending.extend((child, depth + 1) for child in item)
    return result


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class Decision:
    call: ToolCall | None = None
    answer: dict | None = None
    metadata: dict = field(default_factory=dict)


class Provider(Protocol):
    def public_config(self) -> dict: ...
    def start_turn(self, context: dict, instructions: str) -> None: ...
    def next_step(self) -> Decision: ...
    def observe(self, call_id: str, observation: dict) -> None: ...
    def feedback(self, message: str) -> None: ...


def check_answer_shape(answer):
    validate(answer, ANSWER_SCHEMA, ANSWER_SCHEMA, "answer")


def empty_selection():
    return {"topics": None, "geographies": None, "periods": None, "entity": None}
