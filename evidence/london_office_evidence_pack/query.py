#!/usr/bin/env python3
"""Explore the curated London office evidence with Python's standard library.

Run from any working directory; files are resolved beside this script::

    python query.py build
    python query.py sources
    python query.py facts --topic vacancy --geography City
    python query.py facts --entity "2 Aldermanbury Square"
    python query.py source SOURCE_ID
    python query.py compare F01 F02
    python query.py check
    python query.py demo

``build`` reads sources.json, facts.json and coverage.json (JSON arrays) and
atomically replaces the generated evidence.sqlite after validation. Other
commands open the database read-only. There is no network access, external
dependency, arbitrary SQL interface or language-model component.

Comparison is deliberately conservative. A shared curated series key, publisher,
metric, unit, geography, geography basis, segment, evidence class and period kind
are required. Only observed, publisher_estimate and survey_estimate statuses with
numeric values and distinct period-end dates qualify. Percentage-valued metrics
produce percentage-point changes; vacancy rates also produce basis-point changes.
The comparison is ordered chronologically and retains the original source links.

``check`` includes two documented public examples: F01=7.4 and F02=7.2 should
compare as -0.2 percentage points; F01 versus F07 must be refused. These checks
validate the visible evidence pack and comparison rules, not model answers.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "evidence.sqlite"
TOPICS = ("rent", "vacancy", "leasing", "pipeline", "macro", "demand", "esg", "events")
COMPARABLE_STATUSES = {"observed", "publisher_estimate", "survey_estimate"}
PERCENT_UNITS = {"percent", "percentage", "%", "percentage point", "percentage points",
                 "percentage_point", "percentage_points", "pp"}
SOURCE_FIELDS = (
    "source_id", "publisher", "title", "url", "published_at", "retrieved_at",
    "format", "access_status", "role", "notes",
)
FACT_FIELDS = (
    "fact_id", "topic", "metric", "entity", "geography", "geography_basis", "segment",
    "value_num", "value_text", "unit", "period_kind", "period_start", "period_end",
    "comparison_start", "comparison_end", "status", "evidence_class", "source_id",
    "locator", "paraphrase", "caveat", "series_key",
)
COVERAGE_FIELDS = ("requirement", "coverage_status", "sources", "notes")
MATCH_FIELDS = (
    "series_key", "metric", "unit", "geography", "geography_basis", "segment",
    "evidence_class", "period_kind",
)


class EvidenceError(Exception):
    """An input or database error suitable for a short CLI explanation."""


class ComparisonRefused(EvidenceError):
    """The evidence does not support the requested numerical comparison."""


def load_array(filename: str) -> list[dict]:
    path = ROOT / filename
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"Cannot read {filename}: {exc}") from exc
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise EvidenceError(f"{filename} must contain an array of objects.")
    return data


def require_fields(row: dict, fields: tuple[str, ...], label: str) -> None:
    missing = sorted(set(fields) - row.keys())
    extra = sorted(row.keys() - set(fields))
    if missing or extra:
        raise EvidenceError(f"{label}: missing fields {missing}; unexpected fields {extra}.")


def require_text(value, label: str, *, nullable: bool = False, blank: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise EvidenceError(f"{label} must be {'a string or null' if nullable else 'a string'}"
                            f"{' (blank allowed)' if blank else ' with content'}.")


def iso_date(value: str, label: str) -> date:
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"{label} must be an ISO date (YYYY-MM-DD).") from exc
    if result.isoformat() != value:
        raise EvidenceError(f"{label} must use YYYY-MM-DD.")
    return result


def validate_rows(sources: list[dict], facts: list[dict], coverage: list[dict]) -> None:
    source_ids: set[str] = set()
    for source in sources:
        require_fields(source, SOURCE_FIELDS, "Source")
        label = f"Source {source['source_id']}"
        for field in SOURCE_FIELDS:
            require_text(source[field], f"{label}.{field}",
                         nullable=field == "published_at", blank=field == "notes")
        if source["source_id"] in source_ids:
            raise EvidenceError(f"Duplicate source ID: {source['source_id']}.")
        source_ids.add(source["source_id"])
        for field in ("published_at", "retrieved_at"):
            if source[field] is not None:
                try:
                    datetime.fromisoformat(source[field].replace("Z", "+00:00"))
                except ValueError as exc:
                    raise EvidenceError(f"{label}.{field} must be an ISO date or datetime.") from exc

    fact_ids: set[str] = set()
    for fact in facts:
        require_fields(fact, FACT_FIELDS, "Fact")
        label = f"Fact {fact['fact_id']}"
        for field in FACT_FIELDS:
            if field != "value_num":
                require_text(fact[field], f"{label}.{field}",
                             nullable=field in {"value_text", "comparison_start", "comparison_end"},
                             blank=field in {"caveat", "series_key"})
        if fact["fact_id"] in fact_ids:
            raise EvidenceError(f"Duplicate fact ID: {fact['fact_id']}.")
        fact_ids.add(fact["fact_id"])
        if fact["source_id"] not in source_ids:
            raise EvidenceError(f"{label} refers to unknown source {fact['source_id']}.")
        if fact["topic"] not in TOPICS:
            raise EvidenceError(f"{label} has unsupported topic {fact['topic']!r}.")
        number = fact["value_num"]
        if number is not None and (isinstance(number, bool) or
                                   not isinstance(number, (int, float)) or not math.isfinite(number)):
            raise EvidenceError(f"{label}.value_num must be a finite number or null.")
        if number is None and not fact["value_text"]:
            raise EvidenceError(f"{label} needs value_num or value_text.")
        start = iso_date(fact["period_start"], f"{label}.period_start")
        end = iso_date(fact["period_end"], f"{label}.period_end")
        if start > end:
            raise EvidenceError(f"{label} has a reversed reporting period.")
        for field in ("comparison_start", "comparison_end"):
            if fact[field] is not None:
                iso_date(fact[field], f"{label}.{field}")
        if fact["comparison_start"] and fact["comparison_end"]:
            if fact["comparison_start"] > fact["comparison_end"]:
                raise EvidenceError(f"{label} has a reversed comparison period.")

    requirements: set[str] = set()
    for item in coverage:
        require_fields(item, COVERAGE_FIELDS, "Coverage")
        for field in ("requirement", "coverage_status", "notes"):
            require_text(item[field], f"Coverage.{field}", blank=field == "notes")
        if item["requirement"] in requirements:
            raise EvidenceError(f"Duplicate coverage requirement: {item['requirement']}.")
        requirements.add(item["requirement"])
        if not isinstance(item["sources"], list) or any(
                not isinstance(sid, str) or sid not in source_ids for sid in item["sources"]):
            raise EvidenceError(f"Coverage {item['requirement']!r} needs an array of known source IDs.")


def build_database() -> None:
    sources = load_array("sources.json")
    facts = load_array("facts.json")
    coverage = load_array("coverage.json")
    validate_rows(sources, facts, coverage)
    handle, name = tempfile.mkstemp(prefix=".evidence-build-", suffix=".sqlite", dir=ROOT)
    os.close(handle)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript("""
                CREATE TABLE sources (
                    source_id TEXT PRIMARY KEY NOT NULL,
                    publisher TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
                    published_at TEXT, retrieved_at TEXT NOT NULL, format TEXT NOT NULL,
                    access_status TEXT NOT NULL, role TEXT NOT NULL, notes TEXT NOT NULL
                );
                CREATE TABLE facts (
                    fact_id TEXT PRIMARY KEY NOT NULL,
                    topic TEXT NOT NULL, metric TEXT NOT NULL, entity TEXT NOT NULL,
                    geography TEXT NOT NULL, geography_basis TEXT NOT NULL, segment TEXT NOT NULL,
                    value_num REAL, value_text TEXT, unit TEXT NOT NULL,
                    period_kind TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
                    comparison_start TEXT, comparison_end TEXT,
                    status TEXT NOT NULL, evidence_class TEXT NOT NULL,
                    source_id TEXT NOT NULL REFERENCES sources(source_id),
                    locator TEXT NOT NULL, paraphrase TEXT NOT NULL, caveat TEXT NOT NULL,
                    series_key TEXT NOT NULL,
                    CHECK (value_num IS NOT NULL OR value_text IS NOT NULL)
                );
                CREATE TABLE coverage (
                    requirement TEXT PRIMARY KEY NOT NULL,
                    coverage_status TEXT NOT NULL, sources TEXT NOT NULL, notes TEXT NOT NULL
                );
                CREATE INDEX facts_topic ON facts(topic);
                CREATE INDEX facts_series ON facts(series_key, period_end);
                CREATE VIEW evidence AS
                    SELECT f.*, s.publisher AS source_publisher, s.title AS source_title,
                           s.url AS source_url, s.published_at AS source_published_at,
                           s.retrieved_at AS source_retrieved_at
                    FROM facts AS f JOIN sources AS s ON s.source_id = f.source_id;
            """)
            with connection:
                connection.executemany(
                    "INSERT INTO sources VALUES (" + ",".join("?" for _ in SOURCE_FIELDS) + ")",
                    [tuple(row[field] for field in SOURCE_FIELDS) for row in sources],
                )
                connection.executemany(
                    "INSERT INTO facts VALUES (" + ",".join("?" for _ in FACT_FIELDS) + ")",
                    [tuple(row[field] for field in FACT_FIELDS) for row in facts],
                )
                connection.executemany("INSERT INTO coverage VALUES (?, ?, ?, ?)", [
                    (row["requirement"], row["coverage_status"], json.dumps(row["sources"]), row["notes"])
                    for row in coverage
                ])
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise EvidenceError("Generated database failed SQLite integrity_check.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise EvidenceError("Generated database has broken source references.")
        os.replace(temporary, DATABASE)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Built {DATABASE.name}: {len(sources)} sources, {len(facts)} facts, "
          f"{len(coverage)} coverage records.")


def open_readonly() -> sqlite3.Connection:
    if not DATABASE.is_file():
        raise EvidenceError("evidence.sqlite is missing. Run: python query.py build")
    connection = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def get_fact(connection: sqlite3.Connection, fact_id: str) -> dict:
    row = connection.execute("SELECT * FROM evidence WHERE fact_id = ?", (fact_id,)).fetchone()
    if row is None:
        raise EvidenceError(f"Unknown fact ID: {fact_id}.")
    return dict(row)


def compare_facts(a: dict, b: dict) -> dict:
    mismatches = [field for field in MATCH_FIELDS if a[field] != b[field]]
    if not a["series_key"].strip() or not b["series_key"].strip():
        mismatches.append("nonempty curated series_key required")
    if a["source_publisher"] != b["source_publisher"]:
        mismatches.append("source publisher")
    if mismatches:
        raise ComparisonRefused("Incompatible evidence: " + ", ".join(mismatches) + ".")
    for fact in (a, b):
        if fact["status"] not in COMPARABLE_STATUSES:
            raise ComparisonRefused(f"{fact['fact_id']} status {fact['status']!r} is not comparable.")
        if fact["value_num"] is None or fact["value_text"] not in (None, ""):
            raise ComparisonRefused(f"{fact['fact_id']} is not an unambiguous numeric observation.")
        if not math.isfinite(fact["value_num"]):
            raise ComparisonRefused(f"{fact['fact_id']} is not finite.")
    end_a = iso_date(a["period_end"], f"{a['fact_id']}.period_end")
    end_b = iso_date(b["period_end"], f"{b['fact_id']}.period_end")
    if end_a == end_b:
        raise ComparisonRefused("The observation periods end on the same date; no time change is established.")
    earlier, later = (a, b) if end_a < end_b else (b, a)
    old = Decimal(str(earlier["value_num"]))
    new = Decimal(str(later["value_num"]))
    delta = new - old
    percent_unit = a["unit"].strip().lower() in PERCENT_UNITS
    result = {
        "earlier": earlier, "later": later, "difference": delta,
        "difference_unit": "percentage points" if percent_unit else a["unit"],
        "basis_points": delta * 100 if percent_unit and a["metric"] == "vacancy_rate" else None,
        "relative_change_percent": None,
    }
    # Comparing two percentages means percentage points, not percent growth.
    if not percent_unit and old != 0:
        result["relative_change_percent"] = delta / abs(old) * 100
    return result


def number(value) -> str:
    result = format(Decimal(str(value)), ".6f").rstrip("0").rstrip(".")
    return result if result not in ("", "-0") else "0"


def show_comparison(result: dict) -> None:
    a, b = result["earlier"], result["later"]
    print(f"{a['metric']} | {a['geography']} | {a['segment']}")
    print(f"{a['fact_id']} ({a['period_start']} to {a['period_end']}): "
          f"{number(a['value_num'])} {a['unit']}")
    print(f"{b['fact_id']} ({b['period_start']} to {b['period_end']}): "
          f"{number(b['value_num'])} {b['unit']}")
    print(f"Change: {number(result['difference'])} {result['difference_unit']}")
    if result["basis_points"] is not None:
        print(f"Equivalent: {number(result['basis_points'])} basis points")
    if result["relative_change_percent"] is not None:
        print(f"Relative change from the earlier value: {number(result['relative_change_percent'])}%")
    print("Calculated from reported values; their rounding and scope limits still apply.")
    for fact in (a, b):
        print(f"Source {fact['source_id']}: {fact['source_url']} | {fact['locator']}")
        if fact["caveat"]:
            print(f"  Caveat: {fact['caveat']}")


def show_fact(fact: dict) -> None:
    value = number(fact["value_num"]) if fact["value_num"] is not None else fact["value_text"]
    print(f"{fact['fact_id']} | {fact['topic']} | {fact['metric']}: {value} {fact['unit']}")
    print(f"  Entity: {fact['entity']} | Geography: {fact['geography']} | Segment: {fact['segment']}")
    print(f"  Geography basis: {fact['geography_basis']}")
    print(f"  Period: {fact['period_kind']}, {fact['period_start']} to {fact['period_end']}")
    print(f"  Status: {fact['status']} | Evidence: {fact['evidence_class']}")
    print(f"  {fact['paraphrase']}")
    print(f"  Source: {fact['source_id']} | {fact['source_publisher']} | "
          f"Published: {fact['source_published_at'] or 'not established'}")
    print(f"  Locator: {fact['locator']} | {fact['source_url']}")
    if fact["caveat"]:
        print(f"  Caveat: {fact['caveat']}")


def literal_contains(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_facts(connection: sqlite3.Connection, args: argparse.Namespace) -> None:
    clauses, values = [], []
    if args.topic:
        clauses.append("topic = ?")
        values.append(args.topic)
    if args.geography:
        clauses.append("geography LIKE ? ESCAPE '\\'")
        values.append(literal_contains(args.geography))
    if args.entity:
        clauses.append("entity LIKE ? ESCAPE '\\'")
        values.append(literal_contains(args.entity))
    query = "SELECT * FROM evidence"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    rows = connection.execute(query + " ORDER BY fact_id", values).fetchall()
    for index, row in enumerate(rows):
        if index:
            print()
        show_fact(dict(row))
    print(f"\n{len(rows)} fact(s).")


def check_database(connection: sqlite3.Connection) -> bool:
    results: list[tuple[str, bool, str]] = []
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    results.append(("SQLite integrity", integrity == ["ok"], "; ".join(integrity)))
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    results.append(("Source foreign keys", not foreign_keys, f"{len(foreign_keys)} violation(s)"))
    sources = [dict(row) for row in connection.execute("SELECT * FROM sources")]
    facts = [dict(row) for row in connection.execute("SELECT * FROM facts")]
    coverage = [dict(row) for row in connection.execute("SELECT * FROM coverage")]
    try:
        for item in coverage:
            item["sources"] = json.loads(item["sources"])
        validate_rows(sources, facts, coverage)
        results.append(("Schema values, uniqueness and required provenance", True,
                        f"{len(sources)} sources; {len(facts)} facts; {len(coverage)} coverage records"))
    except (EvidenceError, json.JSONDecodeError) as exc:
        results.append(("Schema values, uniqueness and required provenance", False, str(exc)))
    evidence_count = connection.execute("SELECT count(*) FROM evidence").fetchone()[0]
    results.append(("Every fact resolves to a source", evidence_count == len(facts),
                    f"{evidence_count}/{len(facts)} linked"))
    try:
        a, b = get_fact(connection, "F01"), get_fact(connection, "F02")
        comparison = compare_facts(a, b)
        expected = (Decimal(str(a["value_num"])) == Decimal("7.4") and
                    Decimal(str(b["value_num"])) == Decimal("7.2") and
                    comparison["difference"] == Decimal("-0.2") and
                    comparison["difference_unit"] == "percentage points" and
                    comparison["basis_points"] == Decimal("-20"))
        results.append(("Public example F01 to F02", expected,
                        "Expected 7.4 to 7.2: -0.2 percentage points / -20 basis points"))
    except EvidenceError as exc:
        results.append(("Public example F01 to F02", False, str(exc)))
    try:
        a, b = get_fact(connection, "F01"), get_fact(connection, "F07")
        try:
            compare_facts(a, b)
        except ComparisonRefused as exc:
            results.append(("Different metrics F01/F07 are refused", True, str(exc)))
        else:
            results.append(("Different metrics F01/F07 are refused", False, "Unexpectedly accepted"))
    except EvidenceError as exc:
        results.append(("Different metrics F01/F07 are refused", False, str(exc)))
    for label, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} | {label} | {detail}")
    return all(passed for _, passed, _ in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the curated London office evidence pack.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Validate JSON arrays and rebuild evidence.sqlite")
    commands.add_parser("sources", help="List sources and publication dates")
    facts_parser = commands.add_parser("facts", help="List facts with provenance")
    facts_parser.add_argument("--topic", choices=TOPICS)
    facts_parser.add_argument("--geography", help="Literal substring, case insensitive for ASCII")
    facts_parser.add_argument("--entity", help="Literal substring, case insensitive for ASCII")
    compare_parser = commands.add_parser("compare", help="Compare compatible observations chronologically")
    compare_parser.add_argument("id_a")
    compare_parser.add_argument("id_b")
    source_parser = commands.add_parser("source", help="Show one source and its evidence records")
    source_parser.add_argument("source_id")
    commands.add_parser("check", help="Check integrity, provenance and documented public examples")
    commands.add_parser("demo", help="Show the F01/F02 vacancy comparison")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            build_database()
            return 0
        with closing(open_readonly()) as connection:
            if args.command == "sources":
                for row in connection.execute("SELECT * FROM sources ORDER BY source_id"):
                    print(f"{row['source_id']} | {row['publisher']} | "
                          f"Published: {row['published_at'] or 'not established'} | {row['title']}")
                    print(f"  {row['url']} | {row['access_status']} | {row['role']}")
            elif args.command == "facts":
                list_facts(connection, args)
            elif args.command == "source":
                row = connection.execute("SELECT * FROM sources WHERE source_id = ?",
                                         (args.source_id,)).fetchone()
                if row is None:
                    raise EvidenceError(f"Unknown source ID: {args.source_id}.")
                print(json.dumps(dict(row), ensure_ascii=False, indent=2))
                for fact in connection.execute(
                        "SELECT * FROM evidence WHERE source_id = ? ORDER BY fact_id", (args.source_id,)):
                    print()
                    show_fact(dict(fact))
            elif args.command in {"compare", "demo"}:
                ids = ("F01", "F02") if args.command == "demo" else (args.id_a, args.id_b)
                show_comparison(compare_facts(get_fact(connection, ids[0]), get_fact(connection, ids[1])))
            elif args.command == "check":
                return 0 if check_database(connection) else 1
    except (EvidenceError, sqlite3.Error, OSError) as exc:
        prefix = "Comparison refused" if isinstance(exc, ComparisonRefused) else "Error"
        print(f"{prefix}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
