"""Contract, evidence scope and refusal tests; no model/API key required."""
from __future__ import annotations

from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_tools as tools
from contract_validation import ContractViolation, validate

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "evidence/london_office_evidence_pack"


class EvidenceToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "inputs/manifest.json").read_text())["sha256"]
        cls.original_facts = json.loads((PACK / "facts.json").read_text())
        cls.original_sources = {s["source_id"]: s for s in json.loads((PACK / "sources.json").read_text())}

    @classmethod
    def tearDownClass(cls):
        for name, expected in cls.manifest.items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                raise AssertionError(f"Original input changed: {name}")

    def invoke(self, name, arguments):
        result = tools.invoke(name, arguments)
        schema = tools.CONTRACT["tools"][name]["response"] if name in tools.CONTRACT["tools"] else tools.CONTRACT["$defs"]["error_envelope"]
        validate(result, schema, tools.CONTRACT)
        return result

    def success(self, name, arguments):
        result = self.invoke(name, arguments)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def failure(self, name, arguments, code):
        result = self.invoke(name, arguments)
        self.assertFalse(result["ok"], result)
        self.assertIsNone(result["data"])
        self.assertEqual(result["error"]["code"], code)

    def test_only_five_logical_tools(self):
        self.assertEqual(set(tools.CONTRACT["tools"]), {
            "search_facts", "get_fact", "compare_facts", "get_source", "coverage_gap"})
        for name in ("execute_sql", "read_file", "build", "search", ""):
            with self.subTest(name=name):
                self.failure(name, {}, "INVALID_ARGUMENT")

    def test_all_39_facts_keep_original_fields_and_complete_sources(self):
        for expected in self.original_facts:
            with self.subTest(fact=expected["fact_id"]):
                fact = self.success("get_fact", {"fact_id": expected["fact_id"]})
                self.assertEqual({key: fact[key] for key in expected}, expected)
                source = self.original_sources[expected["source_id"]]
                self.assertEqual(fact["source"], source)
                self.assertEqual(fact["source_url"], source["url"])
                self.assertEqual(fact["source_published_at"], source["published_at"])
                self.assertTrue(fact["locator"])

    def test_source_retrieval_including_optional_access_qualifications(self):
        for source_id, expected in self.original_sources.items():
            with self.subTest(source=source_id):
                result = self.success("get_source", {"source_id": source_id})
                self.assertEqual({k: result[k] for k in expected}, expected)
                self.assertEqual(result["fact_ids"], sorted(f["fact_id"] for f in self.original_facts
                                                           if f["source_id"] == source_id))
        self.assertEqual(tools.get_source("S17")["data"]["fact_ids"], [])

    def test_search_is_bounded_ordered_and_explicit_about_truncation(self):
        default = self.success("search_facts", {})
        self.assertEqual(default["count"], 20)
        self.assertTrue(default["truncated"])
        self.assertEqual(default["limit"], 20)
        all_facts = self.success("search_facts", {"limit": 100})
        self.assertEqual(all_facts["count"], 39)
        self.assertFalse(all_facts["truncated"])
        self.assertEqual([f["fact_id"] for f in all_facts["facts"]], [f"F{i:02}" for i in range(1, 40)])

    def test_exact_geography_never_expands_city_or_aliases_docklands(self):
        result = self.success("search_facts", {"geography": "city"})
        self.assertEqual([f["fact_id"] for f in result["facts"]], ["F09", "F13"])
        self.assertEqual(self.success("search_facts", {"geography": "Docklands"})["facts"], [])
        result = self.success("search_facts", {"topic": "vacancy", "metric": "VACANCY_RATE", "geography": "central london"})
        self.assertEqual([f["fact_id"] for f in result["facts"]], ["F01", "F02"])

    def test_period_overlap_is_inclusive_and_entity_is_literal_substring(self):
        result = self.success("search_facts", {"topic": "vacancy", "period_start": "2026-03-31",
                                               "period_end": "2026-03-31"})
        self.assertEqual([f["fact_id"] for f in result["facts"]], ["F01", "F03", "F05"])
        result = self.success("search_facts", {"entity": "aldermanbury"})
        self.assertEqual([f["fact_id"] for f in result["facts"]], ["F22", "F23", "F24", "F25", "F26", "F39"])

    def test_sql_metacharacters_and_wildcards_are_literal_values(self):
        for field in ("geography", "entity", "metric"):
            for value in ("' OR 1=1 --", "%", "_", "\\"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.success("search_facts", {field: value})["facts"], [])
        calls = []
        with closing(tools.evidence.open_readonly()) as connection:
            class Spy:
                def execute(self, sql, values=()):
                    calls.append((sql, values))
                    return connection.execute(sql, values)
            tools._search(Spy(), {"geography": "' OR 1=1 --", "limit": 1})
        self.assertTrue(any("' OR 1=1 --" in values for _, values in calls))
        self.assertTrue(all("' OR 1=1 --" not in sql for sql, _ in calls))

    def test_invalid_arguments_do_not_reach_the_database(self):
        bad = [
            ("search_facts", {"limit": value}) for value in (0, -1, 101, True, 1.5, "1", None)
        ] + [
            ("search_facts", {"topic": "unknown"}),
            ("search_facts", {"geography": "  "}),
            ("search_facts", {"entity": "x" * 201}),
            ("search_facts", {"period_start": "2026-02-30"}),
            ("search_facts", {"period_start": "20260201"}),
            ("search_facts", {"period_start": "2026-07-01", "period_end": "2026-06-30"}),
            ("get_fact", {"fact_id": "F01", "sql": "SELECT 1"}),
            ("get_fact", {"fact_id": "../facts.json"}),
            ("get_fact", {}),
            ("get_fact", None),
            ("get_source", {"source_id": "S01", "database": "/tmp/other.sqlite"}),
            ("compare_facts", {"fact_id_a": "F01", "fact_id_b": "F02", "mode": "any"}),
            ("coverage_gap", {"request": ""}),
            ("coverage_gap", {"request": "x" * 1001}),
        ]
        with patch.object(tools.evidence, "open_readonly", side_effect=AssertionError("Database must not open")):
            for name, arguments in bad:
                with self.subTest(name=name, arguments=arguments):
                    self.failure(name, arguments, "INVALID_ARGUMENT")

    def test_unknown_ids_have_not_found_envelopes(self):
        self.failure("get_fact", {"fact_id": "F999"}, "NOT_FOUND")
        self.failure("get_source", {"source_id": "S99"}, "NOT_FOUND")
        self.failure("compare_facts", {"fact_id_a": "F999", "fact_id_b": "F02"}, "NOT_FOUND")

    def test_public_time_comparisons_preserve_units_and_rounding(self):
        for first, second, difference, bps, relative in [
            ("F01", "F02", "-0.2", "-20", None),
            ("F03", "F04", "-0.1", "-10", None),
            ("F05", "F06", "0.1", "10", None),
            ("F07", "F08", "0.2", None, "9.1"),
            ("F15", "F16", "-0.7", None, None),
        ]:
            with self.subTest(first=first, second=second):
                result = self.success("compare_facts", {"fact_id_a": first, "fact_id_b": second})
                self.assertEqual(result["difference"], difference)
                self.assertEqual(result["basis_points"], bps)
                self.assertEqual(result["relative_change_percent"], relative)
                self.assertEqual(result["relative_change_approximate"], relative is not None)
                self.assertEqual(result["earlier"]["fact_id"], first)
                self.assertEqual(result["later"]["fact_id"], second)
                self.assertTrue(result["rounding_caveat"])

    def test_time_comparison_normalizes_reversed_arguments(self):
        forward = tools.compare_facts("F01", "F02")
        self.assertEqual(forward, tools.compare_facts("F02", "F01"))

    def test_time_refusals_are_not_relaxed_by_cross_section_support(self):
        for a, b in [("F01", "F07"), ("F01", "F01"), ("F09", "F10"),
                     ("F17", "F18"), ("F23", "F25"), ("F22", "F26")]:
            with self.subTest(pair=(a, b)):
                self.failure("compare_facts", {"fact_id_a": a, "fact_id_b": b}, "COMPARISON_REFUSED")

    def test_cross_section_has_explicit_group_order_and_same_period(self):
        forward = self.success("compare_facts", {"fact_id_a": "F09", "fact_id_b": "F10", "mode": "cross_section"})
        reverse = self.success("compare_facts", {"fact_id_a": "F10", "fact_id_b": "F09", "mode": "cross_section"})
        self.assertEqual(forward["difference"], "29.96")
        self.assertEqual(reverse["difference"], "-29.96")
        self.assertEqual(forward["difference_unit"], "GBP_per_sq_ft")
        self.assertEqual(forward["a"]["period_end"], forward["b"]["period_end"])
        self.assertEqual(forward["calculation"], "b_minus_a")
        self.assertIsNone(forward["relative_change_percent"])
        self.assertFalse(forward["relative_change_approximate"])
        for a, b in [("F01", "F02"), ("F09", "F09"), ("F17", "F18")]:
            self.failure("compare_facts", {"fact_id_a": a, "fact_id_b": b, "mode": "cross_section"}, "COMPARISON_REFUSED")

    def test_cross_section_rejects_metadata_drift_forecast_range_and_nonfinite(self):
        a = tools.get_fact("F09")["data"]
        b = tools.get_fact("F10")["data"]
        for field, changed in [("period_end", "2026-03-31"), ("unit", "sq_m"),
                               ("comparison_start", "2025-01-01"),
                               ("source_id", "S01"), ("source_publisher", "other"),
                               ("status", "forecast"), ("segment", "All offices"),
                               ("series_key", "other"), ("value_text", "100–110"),
                               ("value_num", float("inf")), ("value_num", None)]:
            with self.subTest(field=field, changed=changed):
                altered = {**b, field: changed}
                with self.assertRaises(tools.evidence.ComparisonRefused):
                    tools._cross_section(a, altered)

    def test_time_series_rejects_range_and_forecast_even_with_matching_series(self):
        a = tools.get_fact("F01")["data"]
        b = tools.get_fact("F02")["data"]
        for changed in ({"status": "forecast"}, {"value_text": "7–8"}, {"value_num": None}):
            with self.subTest(changed=changed):
                with patch.object(tools.evidence, "get_fact", side_effect=[a, {**b, **changed}]):
                    self.failure("compare_facts", {"fact_id_a": "F01", "fact_id_b": "F02"}, "COMPARISON_REFUSED")

    def test_coverage_gap_resolves_only_fixture_scope(self):
        result = self.success("coverage_gap", {"request": "Canary Wharf vacancy in Q2 2026"})
        self.assertEqual(result["assessment"], "no_matching_facts_in_fixture")
        self.assertEqual(result["available_fact_ids"], [])
        self.assertEqual(result["requested_scope"], {
            "topic": "vacancy", "metric": "vacancy_rate", "geography": "Canary Wharf",
            "period_start": "2026-04-01", "period_end": "2026-06-30"})
        self.assertEqual(result["unresolved_fields"], [])
        self.assertIn("not zero market activity", result["message"])
        self.assertEqual(result["coverage_rows"][0]["requirement"], "Vacancy and availability")

    def test_coverage_available_evidence_and_structured_scope(self):
        result = self.success("coverage_gap", {"request": "City Core vacancy in Q2 2026"})
        self.assertEqual(result["available_fact_ids"], ["F04"])
        self.assertEqual(result["assessment"], "matching_facts_in_fixture")
        result = self.success("coverage_gap", {"request": "Check this explicit scope", "topic": "rent",
            "metric": "average_grade_a_rent", "geography": "West End", "period_start": "2026-01-01",
            "period_end": "2026-06-30"})
        self.assertEqual(result["available_fact_ids"], ["F10"])
        self.assertEqual(result["assessment"], "matching_facts_in_fixture")

    def test_coverage_does_not_alias_availability_to_vacancy(self):
        result = self.success("coverage_gap", {"request": "Central London availability in Q2 2026"})
        self.assertEqual(result["requested_scope"]["metric"], "availability_rate")
        self.assertEqual(result["available_fact_ids"], [])
        self.assertEqual(result["assessment"], "no_matching_facts_in_fixture")

    def test_explicit_topic_wide_coverage_can_establish_absence_without_inventing_metric(self):
        result = self.success("coverage_gap", {"request": "Fixture coverage for one comparison period",
            "topic": "leasing", "geography": "Central London", "period_start": "2025-10-01", "period_end": "2025-12-31"})
        self.assertEqual(result["assessment"], "no_matching_facts_in_fixture")
        self.assertEqual(result["unresolved_fields"], [])
        self.assertNotIn("metric", result["requested_scope"])

    def test_ambiguous_conflicting_or_unsupported_prose_needs_scope(self):
        for request in ["What coverage exists?", "Docklands vacancy in Q2 2026",
                        "City Core and City Fringe vacancy in Q2 2026", "City vacancy in Q1 and Q2 2026",
                        "Canary Wharf vacancy in Q2 2026 excluding Grade B", "not Canary Wharf vacancy in Q2 2026",
                        "Canary Wharf vacancy and availability in Q2 2026"]:
            with self.subTest(request=request):
                result = self.success("coverage_gap", {"request": request})
                self.assertEqual(result["assessment"], "needs_scope")
                self.assertTrue(result["unresolved_fields"])
        result = self.success("coverage_gap", {"request": "Canary Wharf vacancy in Q2 2026", "geography": "City"})
        self.assertIn("geography", result["unresolved_fields"])
        self.assertEqual(result["assessment"], "needs_scope")

    def test_missing_and_invalid_database_fail_closed_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.sqlite"
            with patch.object(tools.evidence, "DATABASE", missing):
                self.failure("get_fact", {"fact_id": "F01"}, "EVIDENCE_UNAVAILABLE")
            self.assertFalse(missing.exists())
            invalid = Path(temporary) / "invalid.sqlite"
            invalid.write_bytes(b"not a database")
            with patch.object(tools.evidence, "DATABASE", invalid):
                self.failure("search_facts", {}, "EVIDENCE_UNAVAILABLE")

    def test_readonly_connection_refuses_writes(self):
        with closing(tools.evidence.open_readonly()) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM facts WHERE fact_id = ?", ("F01",))

    def test_incomplete_response_is_not_published_as_success(self):
        with patch.object(tools, "_decorate", return_value={"fact_id": "F01"}):
            self.failure("get_fact", {"fact_id": "F01"}, "EVIDENCE_UNAVAILABLE")

    def test_runtime_never_reads_fixture_json_or_chat_oracle(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("Runtime must query SQLite")):
            self.success("search_facts", {"limit": 1})
            self.success("get_fact", {"fact_id": "F01"})
            self.success("get_source", {"source_id": "S17"})
            self.success("compare_facts", {"fact_id_a": "F01", "fact_id_b": "F02"})
            self.success("coverage_gap", {"request": "Canary Wharf vacancy in Q2 2026"})

    def test_schema_rejects_missing_provenance_and_invalid_envelopes(self):
        valid = tools.get_fact("F01")
        for field in ("locator", "source", "source_url", "geography_basis", "period_end"):
            candidate = copy.deepcopy(valid)
            del candidate["data"][field]
            with self.subTest(field=field), self.assertRaises(ContractViolation):
                validate(candidate, tools.CONTRACT["tools"]["get_fact"]["response"], tools.CONTRACT)
        for candidate in [{**valid, "ok": 1}, {**valid, "error": {}}, {**valid, "contract_version": "other"}]:
            with self.assertRaises(ContractViolation):
                validate(candidate, tools.CONTRACT["tools"]["get_fact"]["response"], tools.CONTRACT)

    def test_frozen_golden_request_response_pairs(self):
        cases = json.loads((ROOT / "contracts/golden-cases.json").read_text())
        self.assertEqual(cases["contract_version"], tools.VERSION)
        self.assertEqual(tools.GROUPS["contract_version"], tools.VERSION)
        self.assertEqual(cases["fixture_manifest_sha256"], hashlib.sha256((ROOT / "inputs/manifest.json").read_bytes()).hexdigest())
        self.assertGreaterEqual(len(cases["cases"]), 10)
        for case in cases["cases"]:
            with self.subTest(case=case["id"]):
                request = case["request"]
                self.assertEqual(self.invoke(request["tool"], request["arguments"]), case["expected"])


class CLITests(unittest.TestCase):
    def cli(self, arguments, expected):
        with tempfile.TemporaryDirectory() as other_cwd:
            result = subprocess.run([sys.executable, str(ROOT / "agent_tools.py"), *arguments],
                                    cwd=other_cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], expected == 0)
        return payload

    def test_cli_runs_from_another_working_directory(self):
        self.assertEqual(self.cli(["get_fact", "--fact-id", "F01"], 0)["data"]["fact_id"], "F01")
        result = self.cli(["compare_facts", "--fact-id-a", "F09", "--fact-id-b", "F10", "--mode", "cross_section"], 0)
        self.assertEqual(result["data"]["difference"], "29.96")

    def test_cli_refusals_and_usage_errors_are_json_with_exit_2(self):
        for arguments in [[], ["execute_sql"], ["get_fact"], ["get_fact", "--fact-id", "F01", "--sql", "SELECT 1"],
                          ["search_facts", "--limit", "text"],
                          ["compare_facts", "--fact-id-a", "F01", "--fact-id-b", "F07"]]:
            with self.subTest(arguments=arguments):
                self.cli(arguments, 2)

    def test_raw_json_rejects_duplicates_nonfinite_values_and_mixed_flags(self):
        for raw in ['{"limit":1,"limit":2}', '{"limit":NaN}', '{"limit":true}', '[]', '{', 'x' * 8193]:
            with self.subTest(raw=raw[:80]):
                self.assertEqual(self.cli(["search_facts", "--arguments-json", raw], 2)["error"]["code"], "INVALID_ARGUMENT")
        self.cli(["get_fact", "--arguments-json", '{"fact_id":"F01"}', "--fact-id", "F02"], 2)
        self.assertTrue(self.cli(["get_fact", "--arguments-json", '{"fact_id":"F01"}'], 0)["ok"])


if __name__ == "__main__":
    unittest.main()
