"""Apple process boundary and host invariants; every model invocation is mocked."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent
import apple_provider as apple
from apple_worker import generation_schema, answer_generation_schema, normalize_answer, final_selection, calendar_text, explicit_period_window, comparison_candidates, normalize_request_plan, scoped_evidence, selection_from_plan, validate_reference_selection
from agent_protocol import ANSWER_SCHEMA, check_answer_shape
from agent_loop import EvidenceAgent, Ledger, TraceWriter
from agent_protocol import ProviderError, empty_selection
from scripts.check_cp4_trace import check_trace
import agent_tools


class MissingComparisonPeriodTests(unittest.TestCase):
    def context(self, prompt):
        return {"user_message": prompt, "state": {"periods": [], "previous_answer_items": []},
                "known_evidence": Ledger().context()}

    def plan(self):
        return {"task": "compare", "geographies": ["Central London"], "topic": "unspecified",
                "topics": ["vacancy", "leasing"], "entity": "", "count": 0}

    def gather(self, context, plan):
        from apple_worker import comparison_period_status
        ledger, calls = Ledger(), []
        for index in range(12):
            context["known_evidence"] = ledger.context()
            status = comparison_period_status(context, plan)
            if status is None or not status["next_call"]:
                return status, calls
            name, arguments = status["next_call"]
            result = agent_tools.invoke(name, arguments)
            self.assertTrue(result["ok"], result)
            calls.append((name, arguments, result))
            ledger.ingest({"observation_id": f"T1.O{index+1}", "tool": name,
                           "arguments": arguments, "result": result})
        self.fail("Comparison coverage did not converge")

    def test_q2_q3_returns_observed_q2_and_two_confirmed_q3_gaps(self):
        context, plan = self.context("What changed in Central London between Q2 and Q3 2026?"), self.plan()
        status, calls = self.gather(context, plan)
        self.assertTrue(status["complete"])
        self.assertEqual([name for name, _, _ in calls], ["search_facts", "coverage_gap", "search_facts", "coverage_gap"])
        self.assertEqual({r for r in status["references"] if r.startswith("fact:")}, {"fact:F02", "fact:F08"})
        for name, args, result in calls:
            if name == "coverage_gap":
                self.assertEqual((args["period_start"], args["period_end"]), ("2026-07-01", "2026-09-30"))
                self.assertEqual(result["data"]["assessment"], "no_matching_facts_in_fixture")
        _, refs, complete, minimum, _ = scoped_evidence(context, plan)
        self.assertTrue(complete)
        self.assertEqual(minimum, 4)
        validate_reference_selection(context, plan, {"references": refs}, refs, minimum)
        with self.assertRaises(ProviderError):
            validate_reference_selection(context, plan, {"references": [r for r in refs if r.startswith("fact:")]}, refs, minimum)

    def test_nonadjacent_quarters_do_not_publish_intermediate_quarter_facts(self):
        status, _ = self.gather(self.context("Compare Central London between Q1 and Q3 2026"), self.plan())
        self.assertEqual({r for r in status["references"] if r.startswith("fact:")}, {"fact:F01", "fact:F07"})

    def test_available_q1_q2_returns_to_normal_comparison_path(self):
        status, calls = self.gather(self.context("What changed in Central London between Q1 and Q2 2026?"), self.plan())
        self.assertIsNone(status)
        self.assertEqual([name for name, _, _ in calls], ["search_facts", "search_facts"])

    def test_both_periods_missing_closes_for_each_topic_and_year(self):
        for year in (2025, 2026):
            with self.subTest(year=year):
                context = self.context(f"What changed in Central London between Q3 and Q4 {year}?")
                status, calls = self.gather(context, self.plan())
                self.assertTrue(status["complete"])
                self.assertEqual(len(calls), 4)
                self.assertEqual(len(status["references"]), 2)
                self.assertTrue(all(ref.startswith("coverage:") for ref in status["references"]))
                for name, args, result in calls:
                    self.assertTrue(args["period_start"].startswith(str(year)))
                    if name == "coverage_gap":
                        self.assertEqual(result["data"]["assessment"], "no_matching_facts_in_fixture")
                        self.assertEqual(result["data"]["unresolved_fields"], [])
                validate_reference_selection(context, self.plan(), {"references": status["references"]}, status["references"], 2)

    def test_month_request_replaces_prior_dates_and_cannot_reuse_quarterly_figures(self):
        context = self.context("What changed in Central London between May and July 2026?")
        context["state"].update(periods=[{"start":"2026-04-01", "end":"2026-09-30"}],
                                topics=["vacancy", "leasing"], geographies=["Central London"], entity=None)
        self.assertEqual(explicit_period_window(context), {"period_start":"2026-05-01", "period_end":"2026-07-31"})
        plan = {**self.plan(), "task":"brief", "clarification":"none"}
        normalized = normalize_request_plan(plan, context)
        self.assertEqual((normalized["task"], normalized["clarification"]), ("clarify", "period_granularity"))

    def test_geography_followup_preserves_separate_periods_and_compares_available_series(self):
        initial = self.context("What changed in Central London between Q1 and Q2 2026?")
        selected = selection_from_plan(initial, self.plan())
        self.assertEqual(len(selected["periods"]), 2)
        context = self.context("Focus on City Core and City Fringe. Did they move in the same direction?")
        context["state"].update(selected)
        plan = {**self.plan(), "geographies": ["City Core", "City Fringe"]}
        status, calls = self.gather(context, plan)
        self.assertTrue(status["complete"])
        self.assertLessEqual(len(calls), 10)
        self.assertEqual(sum(ref.startswith("comparison:") for ref in status["references"]), 2)
        validate_reference_selection(context, plan, {"references": status["references"]}, status["references"], len(status["references"]))
        context["user_message"] = "Give me two cited points for our meeting."
        context["state"]["previous_answer_items"] = final_selection("answer", {"references":status["references"], "selection":selected})["items"]
        brief = {**plan, "task":"brief", "count":2}
        _,refs,complete,minimum,_ = scoped_evidence(context, brief)
        self.assertTrue(complete)
        comparisons = [ref for ref in refs if ref.startswith("comparison:")]
        validate_reference_selection(context, brief, {"references":comparisons}, refs, minimum)

    def test_worker_keeps_gateway_arguments_for_a_forced_partial_comparison(self):
        from types import SimpleNamespace
        from apple_worker import comparison_period_status, generate
        context = self.context("Focus on City Core and City Fringe. Did they move in the same direction?")
        context["state"].update(periods=[{"start":"2026-01-01", "end":"2026-03-31"},
                                         {"start":"2026-04-01", "end":"2026-06-30"}],
                                topics=["vacancy","leasing"], geographies=["Central London"])
        context["current_turn_tools"] = []
        plan = {**self.plan(), "geographies":["City Core","City Fringe"], "clarification":"none"}
        ledger = Ledger()
        for index in range(12):
            context["known_evidence"] = ledger.context()
            name, args = comparison_period_status(context, plan)["next_call"]
            if name == "compare_facts": break
            ledger.ingest({"observation_id":f"T2.O{index+1}", "tool":name, "arguments":args,
                           "result":agent_tools.invoke(name,args)})
        else: self.fail("No available series comparison was requested")
        class Model:
            context_size = 8192
            async def token_count(self, *args, **kwargs): return 1
        class Session:
            def __init__(self, **kwargs): pass
            async def respond(self, *args, **kwargs):
                return SimpleNamespace(to_json=lambda:json.dumps({"action":"compare_facts", "filters":[]}))
        fm = SimpleNamespace(LanguageModelSession=Session, GenerationOptions=lambda **kwargs:kwargs,
                             SamplingMode=SimpleNamespace(greedy=lambda:"greedy"))
        result = asyncio.run(generate(fm, Model(), {"instructions":"policy", "prompt":json.dumps(context),
                                                  "max_output_tokens":1024, "plan":plan}, {}))
        self.assertEqual(result["decision"], {"type":"compare_facts", "value":args})


def macro_context():
    ledger = Ledger()
    for fact_id in ("F32", "F33", "F34", "F35"):
        result = agent_tools.get_fact(fact_id)
        ledger.ingest({"observation_id": "fixture-" + fact_id, "tool": "get_fact",
                       "arguments": {"fact_id": fact_id}, "result": result})
    state = {"topics": ["macro"], "geographies": [],
             "periods": [], "entity": None, "evidence_ids": list(ledger.known_fact_ids),
             "previous_answer_items": []}
    return {"user_message": "Separate London observations from UK-wide context.",
            "state": state, "known_evidence": ledger.context()}


def metadata():
    return {"provider": "apple", "returned_model": apple.MODEL, "framework": "FoundationModels",
            "execution": "on_device", "availability": "available", "context_size": 8192,
            "os_version": "TEST_MACOS", "model_revision_exposed": False, "model_revision": None,
            "sdk_package": "apple-fm-sdk", "sdk_version": "0.2.1", "sdk_library_sha256": {"test.dylib": "0" * 64}, "xcode_version": "TEST_SDK",
            "response_id": "apple-local-TEST", "response_id_origin": "local_generation_uuid"}


def wire(name, value):
    return {"ok": True, "metadata": metadata(), "decision": {"type": name, "value": value}}


def provider():
    instance = object.__new__(apple.AppleFoundationModelsProvider)
    instance.model, instance.timeout, instance.max_output_tokens = apple.MODEL, 90, 1024
    instance.identity = metadata()
    instance._context, instance._pending, instance._history, instance._feedback = {}, None, [], []
    instance._ledger = Ledger()
    return instance


def final_answer():
    return {"kind": "answer", "items": [{"kind": "fact", "ref": "F01"}],
            "clarification": None, "follow_up": None, "selection": empty_selection()}


class AppleProviderTests(unittest.TestCase):
    def test_generation_projection_preserves_original_host_validation(self):
        original = copy.deepcopy(ANSWER_SCHEMA)
        projected = generation_schema(ANSWER_SCHEMA)
        self.assertEqual(ANSWER_SCHEMA, original)
        self.assertEqual(projected["properties"]["selection"]["properties"]["topics"]["type"], "array")
        self.assertNotIn("topics", projected["properties"]["selection"]["required"])
        invalid = final_answer()
        invalid["items"][0]["ref"] = "x" * 81
        with self.assertRaises(Exception): check_answer_shape(invalid)

    def test_final_branches_restore_only_protocol_nulls(self):
        schema = answer_generation_schema("answer")
        self.assertNotIn("clarification", schema["properties"])
        schema = answer_generation_schema("answer", ["fact:F01"])
        self.assertEqual(schema["properties"]["references"]["items"]["enum"], ["fact:F01"])
        value = normalize_answer({"kind": "answer", "items": [{"kind": "fact", "ref": "F01"}], "selection": {}})
        check_answer_shape(value)
        self.assertEqual(value["selection"], empty_selection())
        clarification = normalize_answer({"kind": "clarify", "items": [], "clarification": "referent", "selection": {}})
        check_answer_shape(clarification)
        self.assertEqual(set(answer_generation_schema("clarify")["properties"]), {"clarification"})

    def test_every_nested_apple_object_has_title_and_exact_property_order(self):
        import agent_tools
        schemas = [tool["parameters"] for tool in agent_tools.CONTRACT["tools"].values()]
        schemas += [answer_generation_schema(kind, ["fact:F01"]) for kind in ("answer", "clarify", "refuse")]
        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertTrue(value["title"])
                    self.assertEqual(value["x-order"], list(value["properties"]))
                for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        for schema in schemas: visit(generation_schema(schema))

    def test_structured_calendar_and_observed_reference_projection(self):
        value = final_selection("answer", {"references": ["comparison:T1.O2"], "selection": {
            "periods": [{"start": {"year": 2026, "month": 1, "day": 1}, "end": {"year": 2026, "month": 6, "day": 30}}]}})
        self.assertEqual(value["items"], [{"kind": "comparison", "ref": "T1.O2"}])
        self.assertEqual(value["selection"]["periods"], [{"start": "2026-01-01", "end": "2026-06-30"}])
        check_answer_shape(value)
        with self.assertRaises(ProviderError): calendar_text({"year": "2026", "month": 1, "day": 1})

    def test_xcode_python_39_launcher_is_rejected_before_environment_changes(self):
        from scripts import install
        with patch.object(install.sys, "version_info", (3, 9, 6)), patch.object(install, "ensure_environment") as prepare:
            with self.assertRaisesRegex(RuntimeError, "Xcode's Python 3.9"):
                install.install()
        prepare.assert_not_called()

    def test_explicit_quarters_normalize_calendar_without_market_values(self):
        context = {"user_message": "Compare Q3 and Q4 2025", "state": {"periods": []}}
        self.assertEqual(explicit_period_window(context), {"period_start": "2025-07-01", "period_end": "2025-12-31"})
        context["user_message"] = "Narrow the geography"
        context["state"]["periods"] = [{"start": "2024-01-01", "end": "2024-06-30"}]
        self.assertEqual(explicit_period_window(context), {"period_start": "2024-01-01", "period_end": "2024-06-30"})
        context["user_message"] = "Compare Q1 2025 and Q2 2026"
        self.assertEqual(explicit_period_window(context), {})  # No silent single-year guess.

    def test_comparison_menu_uses_observed_scope_and_excludes_completed_pairs(self):
        import agent_tools
        ledger = Ledger()
        facts = agent_tools.invoke("search_facts", {"geography": "Central London", "topic": "vacancy"})
        ledger.ingest({"observation_id": "T1.O1", "tool": "search_facts", "result": facts})
        context = {"known_evidence": ledger.context()}
        choices = comparison_candidates(context)
        self.assertEqual(len(choices), 1)
        a, b = choices[0].split("|")[1:]
        comparison = agent_tools.invoke("compare_facts", {"fact_id_a": a, "fact_id_b": b})
        ledger.ingest({"observation_id": "T1.O2", "tool": "compare_facts", "result": comparison})
        self.assertEqual(comparison_candidates({"known_evidence": ledger.context()}), [])

    def test_planner_rejects_hallucinated_places_and_retains_selected_scope(self):
        context = {"user_message": "Give me two cited points for our meeting.", "state": {
            "geographies": ["City Core", "City Fringe"], "topics": ["vacancy"]}}
        plan = {"task": "brief", "geographies": ["New York", "Los Angeles"], "topic": "unspecified", "entity": "meeting", "count": 8, "clarification": "none"}
        value = normalize_request_plan(plan, context)
        self.assertEqual(value["geographies"], ["City Core", "City Fringe"])
        self.assertEqual(value["count"], 2)
        context["user_message"] = "Central London between Q1 and Q2 2026"
        value = normalize_request_plan({**plan, "geographies": ["Central London"], "entity": "Central London"}, context)
        self.assertEqual(value["entity"], "")
        self.assertEqual(value["count"], 0)

    def test_pronoun_is_not_a_geography_and_source_id_is_source_scope(self):
        for places in ([], ["City Core", "City Fringe"]):
            context = {"user_message": "What about there?", "state": {"geographies": places, "topics": []}}
            value = normalize_request_plan({"task": "compare", "geographies": ["there"], "topic": "unspecified", "entity": "there", "count": 0, "clarification": "none"}, context)
            self.assertEqual((value["task"], value["clarification"]), ("clarify", "referent"))
        context["user_message"] = "Show source S17 and its access qualifications."
        value = normalize_request_plan({**value, "task": "compare"}, context)
        self.assertEqual((value["task"], value["geographies"], value["topic"]), ("source", [], "unspecified"))
        context["user_message"] = "Compare Central London vacancy in Q1 and Q2 2026 using S01 and S02"
        value = normalize_request_plan({**value, "task": "compare", "geographies": ["Central London"], "topic": "vacancy"}, context)
        self.assertEqual(value["task"], "compare")

    def test_wrong_geography_cannot_complete_a_narrowed_request(self):
        import agent_tools
        ledger = Ledger()
        ledger.ingest({"observation_id": "T1.O1", "tool": "compare_facts", "result": agent_tools.invoke("compare_facts", {"fact_id_a": "F01", "fact_id_b": "F02"})})
        context = {"user_message": "Focus on City Core and City Fringe", "state": {"periods": [], "previous_answer_items": []}, "known_evidence": ledger.context()}
        plan = {"task": "compare", "geographies": ["City Core", "City Fringe"], "topic": "vacancy", "entity": "", "count": 0}
        facts, refs, complete, _, _ = scoped_evidence(context, plan)
        self.assertEqual((facts, refs, complete), ([], [], False))
        for ids in (("F03", "F04"), ("F05", "F06")):
            ledger.ingest({"observation_id": "T2."+ids[0], "tool": "compare_facts", "result": agent_tools.invoke("compare_facts", {"fact_id_a": ids[0], "fact_id_b": ids[1]})})
            context["known_evidence"] = ledger.context()
            complete = scoped_evidence(context, plan)[2]
            self.assertEqual(complete, ids[0] == "F05")

    def test_absence_closes_a_comparison_without_repeated_gap_calls(self):
        import agent_tools
        ledger = Ledger()
        args = {"request": "Canary Wharf vacancy in Q2 2026", "geography": "Canary Wharf", "topic": "vacancy", "period_start": "2026-04-01", "period_end": "2026-06-30"}
        ledger.ingest({"observation_id": "T1.O2", "tool": "coverage_gap", "result": agent_tools.invoke("coverage_gap", args)})
        context = {"user_message": args["request"], "state": {"periods": [], "previous_answer_items": []}, "known_evidence": ledger.context()}
        plan = {"task": "compare", "geographies": ["Canary Wharf"], "topic": "vacancy", "entity": "", "count": 0}
        self.assertTrue(scoped_evidence(context, plan)[2])
        plan["topic"] = "rent"
        self.assertFalse(scoped_evidence(context, plan)[2])

    def test_intent_classification_uses_separate_policy_from_tool_execution(self):
        from types import SimpleNamespace
        from apple_worker import generate, PLAN_INSTRUCTIONS
        import agent_tools
        policies = []
        outputs = [
            {"task": "lookup", "geographies": [], "topic": "unspecified", "entity": "", "count": 0, "clarification": "none"},
            {"action": "get_fact", "filters": []}, {"fact_id": "F01"}]
        class Session:
            def __init__(self, **kwargs): self.policy = kwargs["instructions"]
            async def respond(self, *args, **kwargs):
                policies.append(self.policy)
                value = outputs.pop(0)
                return SimpleNamespace(to_json=lambda: json.dumps(value))
        class Model:
            context_size = 8192
            async def token_count(self, *args, **kwargs): return 1
        fm = SimpleNamespace(LanguageModelSession=Session, GenerationOptions=lambda **kwargs: kwargs,
                             SamplingMode=SimpleNamespace(greedy=lambda: "greedy"))
        context = {"user_message": "Show fact F01 and its original source.", "state": {"topics": [], "geographies": [], "periods": [], "previous_answer_items": []}, "known_evidence": Ledger().context(), "current_turn_tools": []}
        result = asyncio.run(generate(fm, Model(), {"instructions": "EXECUTION_ONLY", "prompt": json.dumps(context), "max_output_tokens": 1024}, {}))
        self.assertEqual(result["decision"], {"type": "get_fact", "value": {"fact_id": "F01"}})
        self.assertEqual(policies, [PLAN_INSTRUCTIONS, "EXECUTION_ONLY", "EXECUTION_ONLY"])

    def test_single_fact_lookup_completes_without_comparison_or_extra_search(self):
        import agent_tools
        ledger = Ledger()
        ledger.ingest({"observation_id": "T1.O1", "tool": "get_fact", "result": agent_tools.invoke("get_fact", {"fact_id": "F01"})})
        context = {"user_message": "Show fact F01 and its original source.", "state": {"periods": [], "previous_answer_items": []}, "known_evidence": ledger.context()}
        plan = {"task": "lookup", "geographies": [], "topic": "unspecified", "entity": "", "count": 0}
        _, references, complete, _, _ = scoped_evidence(context, plan)
        self.assertTrue(complete)
        self.assertEqual(references, ["fact:F01"])
        self.assertEqual(selection_from_plan(context, plan)["geographies"], ["Central London"])
        self.assertEqual(selection_from_plan(context, plan)["periods"], [{"start": "2026-03-31", "end": "2026-03-31"}])
        # An ID lookup must not be hidden by an earlier search's geography/dates.
        plan.update(geographies=["City Core"], topic="rent")
        context["state"]["periods"] = [{"start": "2026-04-01", "end": "2026-06-30"}]
        self.assertTrue(scoped_evidence(context, plan)[2])
        self.assertEqual(selection_from_plan(context, plan)["topics"], ["vacancy"])

    def test_bare_deictic_with_one_selected_place_reuses_scope(self):
        context = {"user_message": "What about there?", "state": {"geographies": ["City Core"], "topics": ["vacancy"]}}
        plan = {"task": "compare", "geographies": ["there"], "topic": "vacancy", "entity": "there", "count": 0, "clarification": "none"}
        value = normalize_request_plan(plan, context)
        self.assertEqual((value["task"], value["geographies"], value["entity"]), ("brief", ["City Core"], ""))
        context["user_message"] = "What changed there in Q2 2026?"
        value = normalize_request_plan({**plan, "task": "compare", "geographies": []}, context)
        self.assertEqual((value["task"], value["clarification"]), ("clarify", "comparison_baseline"))  # One quarter cannot establish change.

    def test_unnamed_model_topic_cannot_replace_the_selected_market_scope(self):
        context = {"user_message": "What changed in Central London between Q1 and Q2 2026?", "state": {"geographies": [], "topics": []}}
        plan = {"task": "compare", "geographies": ["Central London"], "topic": "macro", "entity": "", "count": 0, "clarification": "none"}
        value = normalize_request_plan(plan, context)
        self.assertEqual(value["topic"], "unspecified")  # Broad change keeps every retrieved series.
        context["state"]["topics"] = ["rent"]
        context["user_message"] = "Give me two cited points for the meeting"
        value = normalize_request_plan({**plan, "task": "brief", "topic": "events", "geographies": []}, context)
        self.assertEqual(value["topic"], "rent")
        context["user_message"] = "What does our newest macro evidence say?"
        value = normalize_request_plan({**plan, "task": "lookup", "topic": "macro", "geographies": []}, context)
        self.assertEqual(value["topic"], "macro")

    def test_generated_reference_selection_must_cover_every_requested_geography(self):
        import agent_tools
        ledger = Ledger()
        for observation_id, ids in [("T1.O1", ("F03", "F04")), ("T1.O2", ("F03", "F04")), ("T1.O3", ("F05", "F06"))]:
            ledger.ingest({"observation_id": observation_id, "tool": "compare_facts", "result": agent_tools.invoke("compare_facts", {"fact_id_a": ids[0], "fact_id_b": ids[1]})})
        context = {"user_message": "Focus on City Core and City Fringe", "state": {"periods": [], "previous_answer_items": []}, "known_evidence": ledger.context()}
        plan = {"task": "compare", "geographies": ["City Core", "City Fringe"], "topic": "vacancy", "entity": "", "count": 2}
        _, allowed, _, minimum, _ = scoped_evidence(context, plan)
        validate_reference_selection(context, plan, {"references": ["comparison:T1.O1", "comparison:T1.O3"]}, allowed, minimum)
        for refs in (["comparison:T1.O1"], ["comparison:T1.O1", "comparison:T1.O2"], ["comparison:T1.O1", "comparison:T1.O1"]):
            with self.assertRaises(ProviderError):
                validate_reference_selection(context, plan, {"references": refs}, allowed, minimum)

    def test_named_publisher_places_and_generic_words_are_not_geographies(self):
        from apple_worker import named_publisher_places, normalize_request_plan
        self.assertEqual(named_publisher_places("Compare City and West End rents"), ["West End", "City"])
        context = {"user_message": "Show one significant office pre-let in our source collection.",
                   "state": {"geographies": [], "topics": []}}
        value = normalize_request_plan({"task": "lookup", "geographies": ["office"], "topic": "unspecified",
                                        "entity": "", "count": 0, "clarification": "none"}, context)
        self.assertEqual(value["geographies"], [])
        self.assertEqual(value["topic"], "leasing")
        context["user_message"] = "What development and refurbishment evidence do we have?"
        value = normalize_request_plan({"task": "coverage", "geographies": [], "topic": "unspecified",
                                        "entity": "", "count": 0, "clarification": "none"}, context)
        self.assertEqual((value["task"], value["topic"]), ("lookup", "pipeline"))
        context["user_message"] = "Trace 2 Aldermanbury Square from pre-let to completion."
        value = normalize_request_plan({"task": "lookup", "geographies": ["Aldermanbury Square"], "topic": "leasing",
                                        "entity": "", "count": 0, "clarification": "none"}, context)
        self.assertEqual((value["geographies"], value["entity"]), ([], "Aldermanbury Square"))

    def test_unspecified_compare_waits_for_each_retrieved_series(self):
        import agent_tools
        ledger = Ledger()
        for topic in ("vacancy", "leasing"):
            ledger.ingest({"observation_id": "S." + topic, "tool": "search_facts",
                           "result": agent_tools.invoke("search_facts", {"geography": "Central London", "topic": topic,
                                                                        "period_start": "2026-01-01", "period_end": "2026-06-30"})})
        context = {"user_message": "What changed in Central London between Q1 and Q2 2026?",
                   "state": {"periods": [], "previous_answer_items": []}, "known_evidence": ledger.context()}
        plan = {"task": "compare", "geographies": ["Central London"], "topic": "unspecified", "entity": "", "count": 0}
        self.assertFalse(scoped_evidence(context, plan)[2])
        ledger.ingest({"observation_id": "C.vacancy", "tool": "compare_facts",
                       "result": agent_tools.invoke("compare_facts", {"fact_id_a": "F01", "fact_id_b": "F02"})})
        context["known_evidence"] = ledger.context()
        self.assertFalse(scoped_evidence(context, plan)[2])
        ledger.ingest({"observation_id": "C.leasing", "tool": "compare_facts",
                       "result": agent_tools.invoke("compare_facts", {"fact_id_a": "F07", "fact_id_b": "F08"})})
        context["known_evidence"] = ledger.context()
        self.assertTrue(scoped_evidence(context, plan)[2])
        self.assertEqual(scoped_evidence(context, plan)[3], 2)

    def test_installer_skips_apple_sdk_build_on_linux_and_never_selects_a_provider(self):
        from scripts import install
        with patch.object(install.platform, "system", return_value="Linux"), \
             patch.object(install.platform, "machine", return_value="x86_64"), \
             patch.object(install, "ensure_environment", return_value=Path("/repo/.venv/bin/python")), \
             patch.object(install.subprocess, "run") as run, redirect_stdout(io.StringIO()) as output:
            install.install()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-3:], ["-m", "pip", "check"])
        self.assertIn("no automatic fallback", output.getvalue())

    def test_incomplete_venv_is_preserved_before_recreation(self):
        from scripts import install
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / ".venv"
            destination.mkdir()
            (destination / "failed-evidence").write_bytes(b"keep this incomplete environment")
            with patch.object(install, "ROOT", root), \
                 patch.object(install, "environment_healthy", side_effect=[False, True]), \
                 patch.object(install.venv, "EnvBuilder") as builder, redirect_stdout(io.StringIO()) as output:
                builder.return_value.create.side_effect = lambda path: path.mkdir()
                install.ensure_environment(destination)
            backups = list((root / "output/venv-backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "failed-evidence").read_bytes(), b"keep this incomplete environment")
            self.assertTrue(destination.is_dir())
            self.assertIn("PRESERVED", output.getvalue())

    def test_complete_venv_is_reused_and_failed_creation_stays_visible(self):
        from scripts import install
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / ".venv"
            with patch.object(install, "environment_healthy", return_value=True), \
                 patch.object(install.venv, "EnvBuilder") as builder:
                install.ensure_environment(destination)
                builder.assert_not_called()
            with patch.object(install, "environment_healthy", return_value=False), \
                 patch.object(install.venv, "EnvBuilder") as builder:
                builder.return_value.create.side_effect = lambda path: path.mkdir()
                with self.assertRaisesRegex(RuntimeError, "New .venv is incomplete"):
                    install.ensure_environment(destination)
                self.assertTrue(destination.is_dir())

    def test_health_check_requires_pip_activation_and_config(self):
        from scripts import install
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with patch.object(install.platform, "system", return_value="Linux"):
                (destination / "bin").mkdir()
                (destination / "bin/python").touch()
                with patch.object(install.subprocess, "run") as run:
                    self.assertFalse(install.environment_healthy(destination))
                    run.assert_not_called()
                (destination / "bin/activate").touch()
                (destination / "pyvenv.cfg").touch()
                for code, expected in ((1, False), (3, False), (0, True)):
                    with patch.object(install.subprocess, "run", return_value=subprocess.CompletedProcess([], code)):
                        self.assertEqual(install.environment_healthy(destination), expected)

    def test_optional_apple_requirement_pins_official_package_and_platform(self):
        from agent_protocol import ROOT
        requirement = (ROOT / "requirements-apple.lock").read_text()
        self.assertIn("apple-fm-sdk==0.2.1", requirement)
        self.assertIn('sys_platform == "darwin"', requirement)
        self.assertIn('platform_machine == "arm64"', requirement)
        self.assertIn("--hash=sha256:e1801d68", requirement)

    def test_actual_guided_enum_wire_executes_gateway_and_observes_before_answer(self):
        instance = provider()
        payloads = []
        decisions = [wire("get_fact", {"fact_id": "F01"}), wire("finish", final_answer())]
        def invoke(payload):
            payloads.append(copy.deepcopy(payload))
            return decisions.pop(0)
        with patch.object(instance, "_invoke", side_effect=invoke):
            result = EvidenceAgent(instance).ask("Show F01")
        self.assertEqual(result["status"], "answered")
        self.assertIn("7.4 percent", result["answer"])
        second = json.loads(payloads[1]["prompt"])
        self.assertEqual(second["known_evidence"]["facts"][0]["fact_id"], "F01")
        self.assertEqual(second["current_turn_tools"][0]["observation_id"], "T1.O1")
        self.assertIn("Never invent IDs", payloads[0]["instructions"])
        self.assertNotIn("chat_cases", json.dumps(payloads))

    def test_null_optional_tool_arguments_are_omitted_and_unknown_fields_rejected(self):
        instance = provider()
        instance.start_turn({"known_evidence": Ledger().context()}, "policy")
        with patch.object(instance, "_invoke", return_value=wire("search_facts", {"geography": "City Core", "topic": None})):
            decision = instance.next_step()
        self.assertEqual(json.loads(decision.call.arguments), {"geography": "City Core"})
        with self.assertRaisesRegex(ProviderError, "TOOL_RESULT_REQUIRED"): instance.next_step()
        with self.assertRaisesRegex(ProviderError, "TOOL_CALL_ID_MISMATCH"): instance.observe("wrong", {})
        for name, value in [("search_facts", {"sql": "DELETE"}), ("execute_sql", {}), ("finish", "prose")]:
            instance = provider()
            instance.start_turn({"known_evidence": Ledger().context()}, "policy")
            with patch.object(instance, "_invoke", return_value=wire(name, value)):
                with self.assertRaisesRegex(ProviderError, "APPLE_INVALID_DECISION"): instance.next_step()

    def test_reset_does_not_carry_tool_history_or_old_facts(self):
        instance = provider()
        with patch.object(instance, "_invoke", side_effect=[wire("get_fact", {"fact_id": "F01"}), wire("finish", final_answer())]):
            EvidenceAgent(instance).ask("Show F01")
        instance.start_turn({"known_evidence": Ledger().context()}, "policy")
        self.assertEqual(instance._known()["facts"], [])
        self.assertEqual(instance._history, [])
        self.assertEqual(instance._feedback, [])

    def test_subprocess_has_no_api_keys_and_errors_are_sanitized_not_retried(self):
        instance = provider()
        good = subprocess.CompletedProcess([], 0, json.dumps(wire("get_fact", {"fact_id": "F01"})), "")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "SECRET", "MODEL_API_KEY": "SECRET"}), \
             patch.object(apple.subprocess, "run", return_value=good) as run:
            instance._invoke({"instructions": "policy", "prompt": "data"})
        forwarded = run.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", forwarded)
        self.assertNotIn("MODEL_API_KEY", forwarded)
        cases = [(subprocess.CompletedProcess([], 0, '{"ok":false,"error":"SECRET"}', "SECRET"), "APPLE_HELPER_FAILED"),
                 (subprocess.CompletedProcess([], 1, "SECRET", "SECRET"), "APPLE_HELPER_FAILED"),
                 (subprocess.CompletedProcess([], 0, "SECRET", "SECRET"), "APPLE_INVALID_HELPER_RESPONSE")]
        for response, code in cases:
            with patch.object(apple.subprocess, "run", return_value=response) as run:
                with self.assertRaisesRegex(ProviderError, code): instance._invoke({})
                self.assertEqual(run.call_count, 1)
        with patch.object(apple.subprocess, "run", side_effect=subprocess.TimeoutExpired("SECRET", 90)):
            with self.assertRaisesRegex(ProviderError, "APPLE_TIMEOUT"): instance._invoke({})

    def test_unavailable_platform_and_missing_sdk_are_rejected(self):
        with patch.object(apple.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ProviderError, "APPLE_REQUIRES_APPLE_SILICON_MAC"):
                apple.AppleFoundationModelsProvider()
        instance = provider()
        response = subprocess.CompletedProcess([], 0, '{"ok":false,"error":"APPLE_SDK_NOT_INSTALLED"}', "")
        with patch.object(apple.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(ProviderError, "APPLE_SDK_NOT_INSTALLED"): instance._invoke(probe=True)

    def test_explicit_apple_failure_never_uses_openai_or_scripted(self):
        with patch.object(agent, "load_environment", return_value={}), \
             patch.object(agent, "AppleFoundationModelsProvider", side_effect=ProviderError("APPLE_MODEL_NOT_READY")) as factory, \
             patch.object(agent, "OpenAIResponsesProvider") as openai, \
             redirect_stderr(io.StringIO()) as errors, redirect_stdout(io.StringIO()):
            self.assertEqual(agent.main(["--provider", "apple", "--message", "Show F01"]), 2)
        factory.assert_called_once()
        openai.assert_not_called()
        self.assertIn("APPLE_MODEL_NOT_READY", errors.getvalue())

    def test_trace_binds_apple_route_and_rejects_wrong_expected_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "apple.jsonl"
            instance = provider()
            from agent_loop import run_identity
            identity = run_identity()
            identity["source_dirty"] = False  # Test-only recorded identity; not real live proof.
            with patch("agent_loop.run_identity", return_value=identity), \
                 patch.object(instance, "_invoke", side_effect=[wire("get_fact", {"fact_id": "F01"}), wire("finish", final_answer())]):
                trace = TraceWriter(path, instance.public_config())
                EvidenceAgent(instance, trace).ask("Show F01")
                trace.close()
            self.assertEqual(check_trace(path, True, "apple")["answered"], 1)
            with self.assertRaisesRegex(ValueError, "does not match"): check_trace(path, True, "openai")
            events = [json.loads(line) for line in path.read_text().splitlines()]
            events[0]["provider"]["sdk_version"] = "wrong"
            path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
            with self.assertRaisesRegex(ValueError, "SDK identity"): check_trace(path, True, "apple")



class NeoIntegrationRegressionTests(unittest.TestCase):
    def test_compact_decisions_keep_qualifications_and_do_not_mutate_ledger(self):
        from apple_worker import compact_decision_context
        import agent_tools
        ledger=Ledger()
        ledger.ingest({'observation_id':'epc','tool':'get_fact','result':agent_tools.invoke('get_fact',{'fact_id':'F30'})})
        context={'user_message':'Inspect this certificate','request_plan':{'task':'lookup'},'known_evidence':ledger.context()}
        before=copy.deepcopy(context)
        compact=compact_decision_context(context,context['known_evidence']['facts'],{},[])
        fact=compact['known_evidence']['facts'][0]
        self.assertIn('Second floor only',fact['caveat'])
        self.assertEqual(fact['geography_basis'],'assessed_premises')
        self.assertEqual(fact['period_kind'],context['known_evidence']['facts'][0]['period_kind'])
        self.assertTrue(compact['known_evidence']['sources'][0]['notes'])
        self.assertEqual(context,before)

    def test_broad_comparison_cannot_drop_observed_take_up(self):
        from apple_worker import scoped_evidence,validate_reference_selection
        import agent_tools
        ledger=Ledger()
        for oid,a,b in [('vacancy','F01','F02'),('leasing','F07','F08')]:
            ledger.ingest({'observation_id':oid,'tool':'compare_facts','result':agent_tools.invoke('compare_facts',{'fact_id_a':a,'fact_id_b':b})})
        context={'user_message':'What changed in Central London between Q1 and Q2 2026?',
                 'state':{'periods':[],'previous_answer_items':[]},'known_evidence':ledger.context()}
        plan={'task':'compare','geographies':['Central London'],'topic':'unspecified','entity':'','count':0}
        _,allowed,complete,minimum,_=scoped_evidence(context,plan)
        self.assertTrue(complete)
        with self.assertRaises(ProviderError):
            validate_reference_selection(context,plan,{'references':['comparison:vacancy']},allowed,minimum)
        validate_reference_selection(context,plan,{'references':['comparison:vacancy','comparison:leasing']},allowed,minimum)

class ScopeBreakdownRegressionTests(unittest.TestCase):
    def test_descriptive_country_breakdown_is_not_arithmetic(self):
        from apple_worker import normalize_request_plan
        context={"user_message":"Separate London observations from UK-wide context.","state":{"topics":["macro"],"geographies":[]}}
        plan={"task":"compare","topic":"macro","geographies":["London","UK"],"entity":"","count":0,"clarification":"none"}
        actual=normalize_request_plan(plan,context)
        self.assertEqual(actual["task"],"lookup")
        self.assertEqual(set(actual["geographies"]),{"London","United Kingdom"})

class NeoLiveLoopRegressionTests(unittest.TestCase):
    def test_one_cross_section_covers_both_requested_places(self):
        import agent_tools
        ledger=Ledger()
        ledger.ingest({'observation_id':'rent','tool':'compare_facts','result':agent_tools.invoke('compare_facts',{'fact_id_a':'F09','fact_id_b':'F10','mode':'cross_section'})})
        context={'user_message':'Compare City and West End rents','state':{'periods':[],'previous_answer_items':[]},'known_evidence':ledger.context()}
        plan={'task':'compare','topic':'rent','geographies':['City','West End'],'entity':'','count':0}
        _,refs,complete,minimum,_=scoped_evidence(context,plan)
        self.assertTrue(complete)
        self.assertEqual(minimum,1)
        validate_reference_selection(context,plan,{'references':['comparison:rent']},refs,minimum)

    def test_country_alias_cannot_become_a_building_filter(self):
        context={'user_message':'Separate London observations from UK-wide context.','state':{'topics':['macro'],'geographies':[]}}
        plan={'task':'compare','topic':'macro','geographies':['London','UK'],'entity':'UK','count':0,'clarification':'none'}
        value=normalize_request_plan(plan,context)
        self.assertEqual(value['entity'],'')
        self.assertEqual(value['task'],'lookup')
        context['user_message']='How could this inform our office-market watchlist?'
        value=normalize_request_plan({**plan,'entity':'','geographies':[]},context)
        self.assertEqual(value['task'],'lookup')

class CP5ScopeContinuityTests(unittest.TestCase):
    def context(self,text,topics=None,entity=None):
        return {"user_message":text,"state":{"topics":topics or [],"entity":entity,"geographies":[]}}
    def plan(self,topic="unspecified",entity=""):
        return {"task":"lookup","topic":topic,"entity":entity,"geographies":[],"count":0,"clarification":"none"}
    def test_delivery_request_expands_leasing_scope_and_keeps_property(self):
        from apple_worker import request_topics
        value=normalize_request_plan(self.plan('leasing'),self.context('What is the committed area and expected delivery?',['leasing'],'A named office scheme'))
        self.assertEqual(set(request_topics(value)),{'leasing','pipeline'})
        self.assertEqual(value['entity'],'A named office scheme')
    def test_project_history_clears_inherited_topic_filter(self):
        from apple_worker import request_topics
        value=normalize_request_plan(self.plan('leasing','Aldermanbury Square'),self.context('Trace 2 Aldermanbury Square from pre-let to completion.',['pipeline']))
        self.assertEqual(value['task'],'history')
        self.assertEqual(request_topics(value),[])
    def test_quality_spans_categories_and_certificate_narrows_without_stale_topic(self):
        from apple_worker import request_topics,scope_fact_matches
        context=self.context('What evidence is relevant to quality, sustainability and hybrid working?')
        value=normalize_request_plan(self.plan('esg'),context)
        self.assertEqual(set(request_topics(value)),{'demand','esg'})
        context=self.context('Show a concrete London office energy certificate.',['demand','esg'])
        value=normalize_request_plan(self.plan('demand'),context)
        self.assertEqual(request_topics(value),['esg'])
        value=normalize_request_plan({**self.plan('esg'),'geographies':['London']},context)
        self.assertEqual(value['geographies'],[])
        self.assertTrue(scope_fact_matches({'metric':'epc_rating_and_score'},context,value))
        self.assertFalse(scope_fact_matches({'metric':'reported_breeam_achievement'},context,value))

class ProjectHistoryCompletenessTests(unittest.TestCase):
    def test_topic_limited_search_cannot_close_project_history(self):
        from apple_worker import history_search_complete
        context={"known_evidence":{"searches":[{"arguments":{"topic":"pipeline"},"truncated":False}]}}
        plan={"task":"history","entity":"A named building"}
        self.assertFalse(history_search_complete(context,plan))
        context['known_evidence']['searches'].append({'arguments':{'entity':'named building'},'truncated':False})
        self.assertTrue(history_search_complete(context,plan))
        context['known_evidence']['searches'][-1]['truncated']=True
        self.assertFalse(history_search_complete(context,plan))

class NeoIndependentFailureRegressionTests(unittest.TestCase):
    def test_unnamed_example_uses_observed_publication_date_not_order(self):
        from apple_worker import recent_example_facts
        context={'user_message':'Show one office pre-let example','known_evidence':{'sources':[{'source_id':'old','published_at':'2020-01-01'},{'source_id':'new','published_at':'2025-01-01'}]}}
        rows=[{'source_id':'new','period_start':'2019-01-01'},{'source_id':'old','period_start':'2020-01-01'}]
        plan={'count':1,'entity':''}
        self.assertEqual(recent_example_facts(rows,context,plan),rows[:1])
        self.assertEqual(recent_example_facts(rows,context,{**plan,'entity':'Named scheme'}),rows)

    def test_explicit_fact_address_overrides_recency_default(self):
        from apple_worker import recent_example_facts
        rows=[{'source_id':'source','period_start':'2020-01-01'}]
        context={'user_message':'Show one pre-let, fact F22','known_evidence':{'sources':[]}}
        self.assertEqual(recent_example_facts(rows,context,{'count':1,'entity':''}),rows)

    def test_unique_selection_is_only_available_when_the_set_is_forced(self):
        from apple_worker import unique_reference_selection
        self.assertEqual(unique_reference_selection(['fact:a','fact:b'],2),{'references':['fact:a','fact:b']})
        self.assertIsNone(unique_reference_selection(['fact:a','fact:b'],1))
        self.assertIsNone(unique_reference_selection(['fact:a','fact:a'],2))
        self.assertIsNone(unique_reference_selection([],0))

    def test_macro_ledger_needs_no_reference_generation(self):
        import asyncio,types,json
        from apple_worker import generate
        calls=[]
        class Model:
            context_size=4096
            async def token_count(self,text=None,**kwargs):return 100
        class Session:
            def __init__(self,**kwargs):pass
            async def respond(self,task,**kwargs):
                calls.append(task)
                return types.SimpleNamespace(to_json=lambda:json.dumps({'action':'answer','filters':[]}))
        fm=types.SimpleNamespace(LanguageModelSession=Session,GenerationOptions=lambda **kwargs:kwargs,
                                 SamplingMode=types.SimpleNamespace(greedy=lambda:None))
        context={**macro_context(), 'current_turn_tools':[]}
        plan={'task':'lookup','topic':'macro','geographies':['London','UK'],'entity':'','count':0,'clarification':'none'}
        response=asyncio.run(generate(fm,Model(),{'instructions':'policy','prompt':json.dumps(context),'max_output_tokens':1024,'plan':plan},{}))
        self.assertEqual(len(calls),1) # Action only; no redundant set generation or retry.
        self.assertEqual(response['metadata']['reference_selection_mode'],'host_unique_legal_set')
        self.assertEqual(len(response['decision']['value']['items']),4)

class MacroBreakdownRouteTests(unittest.TestCase):
    def recorded_context(self):
        return macro_context()
    def test_descriptive_breakdown_is_classifier_independent(self):
        from apple_worker import unique_reference_selection
        for task in ('brief','history','lookup','compare'):
            context=self.recorded_context()
            plan=normalize_request_plan({'task':task,'topic':'macro','geographies':['London','UK'],'entity':'','count':0,'clarification':'none'},context)
            _,refs,complete,minimum,_=scoped_evidence(context,plan)
            self.assertEqual(plan['task'],'lookup')
            self.assertTrue(complete)
            self.assertEqual(minimum,4)
            self.assertEqual(len(unique_reference_selection(refs,minimum)['references']),4)
    def test_unlimited_follow_up_brief_keeps_both_geographies(self):
        from apple_worker import unique_reference_selection
        context=self.recorded_context();context['user_message']='How could this inform our office-market watchlist?'
        context['state']['geographies']=['London','United Kingdom']
        plan=normalize_request_plan({'task':'brief','topic':'macro','geographies':[],'entity':'','count':0,'clarification':'none'},context)
        _,refs,complete,minimum,_=scoped_evidence(context,plan)
        self.assertTrue(complete)
        self.assertEqual(minimum,4)
        self.assertIsNotNone(unique_reference_selection(refs,minimum))
    def test_scope_rejection_reports_only_supported_ids_and_structural_details(self):
        context=self.recorded_context()
        plan={'task':'brief','topic':'macro','geographies':['London','United Kingdom'],'entity':'','count':2}
        _,refs,_,minimum,_=scoped_evidence(context,plan)
        with self.assertRaises(ProviderError) as caught:
            validate_reference_selection(context,plan,{'references':['fact:F33','fact:F34']},refs,minimum)
        self.assertEqual(caught.exception.diagnostic['rule'],'scope')
        self.assertEqual(caught.exception.diagnostic['task'],'brief')
        self.assertEqual(caught.exception.diagnostic['selected_supported_references'],['fact:F33','fact:F34'])
        with self.assertRaises(ProviderError) as unknown:
            validate_reference_selection(context,plan,{'references':['DO_NOT_LOG_RAW_MODEL_TEXT']},refs,minimum)
        self.assertNotIn('DO_NOT_LOG',str(unknown.exception.diagnostic))

if __name__ == "__main__": unittest.main()
