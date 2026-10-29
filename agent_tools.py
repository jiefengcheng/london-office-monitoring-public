#!/usr/bin/env python3
"""Five read-only London evidence tools. No model, arbitrary SQL or file tool."""
from __future__ import annotations

import argparse
import calendar
from contextlib import closing
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import importlib.util
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

from contract_validation import ContractViolation, validate

ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((ROOT / "contracts/tool-contract.json").read_text(encoding="utf-8"))
GROUPS = json.loads((ROOT / "contracts/cross-section-groups.json").read_text(encoding="utf-8"))
VERSION = CONTRACT["contract_version"]
_spec = importlib.util.spec_from_file_location(
    "_london_original_query", ROOT / "evidence/london_office_evidence_pack/query.py"
)
evidence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evidence)

MATCH_SEMANTICS = (
    "topic: canonical enum; metric/geography: exact ASCII-case-insensitive; entity: literal ASCII-case-insensitive "
    "substring; periods: inclusive interval overlap; order: fact_id ascending; no geography aliases"
)
ROUNDING_CAVEAT = "Calculated from reported values; their rounding and scope limits still apply."
FILTER_FIELDS = ("topic", "metric", "geography", "entity", "period_start", "period_end")
TOPIC_COVERAGE = {
    "rent": ["Prime and Grade A rents"],
    "vacancy": ["Vacancy and availability"],
    "leasing": ["Leasing activity and take-up", "Development, refurbishment and pre-lets"],
    "pipeline": ["Development, refurbishment and pre-lets"],
    "macro": ["Interest rates, economy and employment"],
    "demand": ["Flight to quality, ESG and hybrid work"],
    "esg": ["Flight to quality, ESG and hybrid work"],
    "events": ["Emerging news and significant events"],
}


class ToolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _failure(code, message):
    return {"contract_version": VERSION, "ok": False, "data": None,
            "error": {"code": code, "message": message}}


def _source(connection, source_id):
    row = connection.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
    if row is None:
        raise ToolError("NOT_FOUND", f"Unknown source ID: {source_id}.")
    return dict(row)


def _decorate(connection, fact):
    return {**fact, "source": _source(connection, fact["source_id"])}


def _decimal_string(value):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _fact(connection, fact_id):
    try:
        return _decorate(connection, evidence.get_fact(connection, fact_id))
    except evidence.EvidenceError as error:
        raise ToolError("NOT_FOUND", str(error)) from error


def _search(connection, arguments):
    # Only these fixed column names can enter SQL. All caller values are parameters.
    clauses, values = [], []
    for field in ("topic", "metric", "geography"):
        if field in arguments:
            clauses.append(f"{field} = ? COLLATE NOCASE")
            values.append(arguments[field])
    if "entity" in arguments:
        clauses.append("entity LIKE ? ESCAPE '\\'")
        values.append(evidence.literal_contains(arguments["entity"]))
    if "period_start" in arguments:
        clauses.append("period_end >= ?")
        values.append(arguments["period_start"])
    if "period_end" in arguments:
        clauses.append("period_start <= ?")
        values.append(arguments["period_end"])
    limit = arguments.get("limit", 20)
    sql = "SELECT * FROM evidence"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = connection.execute(sql + " ORDER BY fact_id LIMIT ?", (*values, limit + 1)).fetchall()
    return {"facts": [_decorate(connection, dict(row)) for row in rows[:limit]],
            "count": min(len(rows), limit), "limit": limit, "truncated": len(rows) > limit,
            "filters": {key: arguments[key] for key in FILTER_FIELDS if key in arguments},
            "match_semantics": MATCH_SEMANTICS}


def _cross_section(a, b):
    pair = {a["fact_id"], b["fact_id"]}
    group = next((g for g in GROUPS["groups"] if set(g["members"]) == pair), None)
    if group is None:
        raise evidence.ComparisonRefused("Pair is not in an approved cross-section group.")
    for fact in (a, b):
        expected = {**group["common"], **group["members"][fact["fact_id"]]}
        if any(fact[key] != value for key, value in expected.items()):
            raise evidence.ComparisonRefused("Fact metadata does not match the approved cross-section group.")
        if (fact["status"] not in evidence.COMPARABLE_STATUSES or fact["value_num"] is None
                or fact["value_text"] not in (None, "") or not math.isfinite(fact["value_num"])):
            raise evidence.ComparisonRefused("Cross-section requires comparable numeric observations.")
    difference = Decimal(str(b["value_num"])) - Decimal(str(a["value_num"]))
    return {"mode": "cross_section", "a": a, "b": b,
            "difference": _decimal_string(difference), "difference_unit": a["unit"],
            "basis_points": None, "relative_change_percent": None,
            "relative_change_approximate": False,
            "calculation": "b_minus_a", "comparison_group": group["group_id"],
            "rounding_caveat": ROUNDING_CAVEAT}


def _compare(connection, arguments):
    a = _fact(connection, arguments["fact_id_a"])
    b = _fact(connection, arguments["fact_id_b"])
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        if arguments.get("mode", "time_series") == "cross_section":
            return _cross_section(a, b)
        result = evidence.compare_facts(a, b)
        relative = result["relative_change_percent"]
        if relative is not None:
            result["relative_change_percent"] = relative.quantize(Decimal("0.1"))
    return {"mode": "time_series", **{key: _decimal_string(value) if isinstance(value, Decimal) else value
                                      for key, value in result.items()},
            "relative_change_approximate": relative is not None,
            "calculation": "later_minus_earlier", "rounding_caveat": ROUNDING_CAVEAT}


def _mentions(text, phrase):
    return list(re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE | re.ASCII))


def _resolve_scope(connection, arguments):
    # A documented narrow parser, not general intent recognition. Explicit filters
    # constrain retrieval; conflicts or unresolved scope prevent any absence claim.
    text = arguments["request"]
    inferred, ambiguous = {}, set()
    metric_signals = [("vacancy", "vacancy", "vacancy_rate"),
                      ("availability", "vacancy", "availability_rate")]
    hits = [item for item in metric_signals if _mentions(text, item[0])]
    topics = {topic for _, topic, _ in hits}
    for word, topic in (("rent", "rent"), ("rents", "rent"), ("leasing", "leasing"),
                        ("pipeline", "pipeline"), ("macro", "macro"), ("ESG", "esg"),
                        ("hybrid", "demand"), ("news", "events")):
        if _mentions(text, word):
            topics.add(topic)
    if len(topics) == 1:
        inferred["topic"] = topics.pop()
    elif topics:
        ambiguous.add("topic")
    metrics = {metric for _, _, metric in hits}
    if len(metrics) == 1:
        inferred["metric"] = metrics.pop()
    elif metrics:
        ambiguous.add("metric")
    spans = []
    places = set()
    geographies = connection.execute("SELECT DISTINCT geography FROM facts ORDER BY length(geography) DESC, geography").fetchall()
    for row in geographies:
        for match in _mentions(text, row[0]):
            if not any(start <= match.start() and match.end() <= end for start, end in spans):
                spans.append(match.span())
                places.add(row[0])
    if len(places) == 1:
        inferred["geography"] = places.pop()
    elif places:
        ambiguous.add("geography")
    quarters = re.findall(r"\bQ([1-4])\s+(\d{4})\b", text, re.IGNORECASE)
    quarter_tokens = re.findall(r"\bQ[1-4]\b", text, re.IGNORECASE)
    if len(quarters) == 1 and len(quarter_tokens) == 1:
        quarter, year = map(int, quarters[0])
        if 1 <= year <= 9999:
            first_month = (quarter - 1) * 3 + 1
            last_month = first_month + 2
            inferred["period_start"] = date(year, first_month, 1).isoformat()
            inferred["period_end"] = date(year, last_month, calendar.monthrange(year, last_month)[1]).isoformat()
    elif quarter_tokens:
        ambiguous.update(("period_start", "period_end"))
    explicit = {key: arguments[key] for key in FILTER_FIELDS if key in arguments}
    conflicts = {key for key in explicit.keys() & inferred.keys()
                 if explicit[key].casefold() != inferred[key].casefold()}
    # Multiple mentions remain ambiguous even with explicit filters; the caller
    # should submit one clarified request rather than silently changing its meaning.
    scope = {**inferred, **explicit}
    required = {"topic", "metric", "geography", "period_start", "period_end"}
    # An explicit topic-wide query is a complete scope across all metrics in
    # that topic. Omitting metric broadens a checked query; it is not ambiguity.
    if {"topic", "geography", "period_start", "period_end"} <= explicit.keys():
        required.discard("metric")
    unresolved = sorted((required - scope.keys()) | ambiguous | conflicts)
    if not required <= explicit.keys():
        # Convenience parsing is only complete for this one documented template.
        # Other prose may contain unsupported constraints (negation, entities,
        # extra dates). It must not license a definitive fixture-absence claim.
        place = inferred.get("geography")
        template = (re.escape(place) + r"\s+(?:vacancy|availability)(?:\s+rate)?"
                    r"\s+(?:in\s+)?Q[1-4]\s+\d{4}\s*\??") if place else None
        if template is None or not re.fullmatch(template, text.strip(), re.IGNORECASE | re.ASCII):
            unresolved = sorted(set(unresolved) | {"request_interpretation"})
    return scope, sorted(inferred.keys() - explicit.keys()), unresolved


def _coverage(connection, arguments):
    scope, inferred_fields, unresolved = _resolve_scope(connection, arguments)
    matched = _search(connection, {**scope, "limit": 100})
    rows = connection.execute("SELECT * FROM coverage ORDER BY requirement").fetchall()
    requirements = TOPIC_COVERAGE.get(scope.get("topic"))
    coverage = [{**dict(row), "sources": json.loads(row["sources"])} for row in rows
                if requirements is None or row["requirement"] in requirements]
    if unresolved:
        assessment = "needs_scope"
        message = "Scope is incomplete, conflicting or ambiguous; no absence claim is established. Provide explicit filters and one clear request."
    elif matched["count"]:
        assessment = "matching_facts_in_fixture"
        message = "Matching facts exist in this curated fixture; this does not establish complete market coverage."
    else:
        assessment = "no_matching_facts_in_fixture"
        message = "No matching facts in this curated fixture for the resolved scope. This is a coverage gap, not zero market activity."
    return {"request": arguments["request"], "requested_scope": scope,
            "inferred_fields": inferred_fields, "unresolved_fields": unresolved,
            "assessment": assessment, "message": message, "coverage_rows": coverage,
            "available_fact_ids": [fact["fact_id"] for fact in matched["facts"]],
            "available_count": matched["count"], "truncated": matched["truncated"],
            "scope_limit": "Curated fixture only; exact publisher geography; only supported keywords and a single Qn YYYY are inferred; explicit filters recommended."}


def invoke(tool_name, arguments):
    """Sole model-facing dispatch. Parameters are JSON values, never SQL/paths."""
    try:
        if not isinstance(tool_name, str) or tool_name not in CONTRACT["tools"]:
            raise ToolError("INVALID_ARGUMENT", "Unknown tool name.")
        validate(arguments, CONTRACT["tools"][tool_name]["parameters"], CONTRACT, "arguments")
        if ("period_start" in arguments and "period_end" in arguments
                and arguments["period_start"] > arguments["period_end"]):
            raise ToolError("INVALID_ARGUMENT", "period_start must not follow period_end.")
        with closing(evidence.open_readonly()) as connection:
            if tool_name == "search_facts":
                data = _search(connection, arguments)
            elif tool_name == "get_fact":
                data = _fact(connection, arguments["fact_id"])
            elif tool_name == "get_source":
                data = {**_source(connection, arguments["source_id"]),
                        "fact_ids": [row[0] for row in connection.execute(
                            "SELECT fact_id FROM facts WHERE source_id = ? ORDER BY fact_id",
                            (arguments["source_id"],))]}
            elif tool_name == "compare_facts":
                data = _compare(connection, arguments)
            else:
                data = _coverage(connection, arguments)
        response = {"contract_version": VERSION, "ok": True, "data": data, "error": None}
        try:
            validate(response, CONTRACT["tools"][tool_name]["response"], CONTRACT)
        except ContractViolation as error:
            raise ToolError("EVIDENCE_UNAVAILABLE", "Evidence does not satisfy the shared response contract.") from error
        return response
    except ContractViolation as error:
        return _failure("INVALID_ARGUMENT", str(error))
    except ToolError as error:
        return _failure(error.code, str(error))
    except evidence.ComparisonRefused as error:
        return _failure("COMPARISON_REFUSED", str(error))
    except (evidence.EvidenceError, sqlite3.Error, OSError, KeyError, ValueError, TypeError, ArithmeticError):
        return _failure("EVIDENCE_UNAVAILABLE", "Evidence database unavailable or invalid; verify the supplied fixture.")


def search_facts(**arguments):
    return invoke("search_facts", arguments)


def get_fact(fact_id):
    return invoke("get_fact", {"fact_id": fact_id})


def compare_facts(fact_id_a, fact_id_b, mode="time_series"):
    return invoke("compare_facts", {"fact_id_a": fact_id_a, "fact_id_b": fact_id_b, "mode": mode})


def get_source(source_id):
    return invoke("get_source", {"source_id": source_id})


def coverage_gap(request, **arguments):
    return invoke("coverage_gap", {"request": request, **arguments})


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError("INVALID_ARGUMENT", message)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToolError("INVALID_ARGUMENT", f"Duplicate argument: {key}.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def main(argv=None):
    parser = _Parser(description=__doc__)
    subparsers = parser.add_subparsers(dest="tool_name", required=True)
    for name, tool in CONTRACT["tools"].items():
        command = subparsers.add_parser(name, help=tool["description"])
        command.add_argument("--arguments-json", help="JSON object instead of individual flags (max 8192 characters)")
        for field, schema in tool["parameters"]["properties"].items():
            command.add_argument("--" + field.replace("_", "-"),
                                 type=int if schema["type"] == "integer" else str)
    try:
        options = vars(parser.parse_args(argv))
        tool_name = options.pop("tool_name")
        raw = options.pop("arguments_json")
        arguments = {key: value for key, value in options.items() if value is not None}
        if raw is not None:
            if arguments:
                raise ToolError("INVALID_ARGUMENT", "Do not mix --arguments-json with individual argument flags.")
            if len(raw) > 8192:
                raise ToolError("INVALID_ARGUMENT", "JSON arguments exceed 8192 characters.")
            try:
                arguments = json.loads(raw, object_pairs_hook=_strict_object,
                                       parse_constant=_reject_constant)
            except ValueError as error:
                raise ToolError("INVALID_ARGUMENT", "Arguments must be valid finite JSON.") from error
        response = invoke(tool_name, arguments)
    except ToolError as error:
        response = _failure(error.code, str(error))
    print(json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
