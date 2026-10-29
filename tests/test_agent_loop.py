"""CP4 mechanics and provider-wire tests. Every HTTP interaction is mocked."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import copy
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import agent
from agent_loop import EvidenceAgent, TraceWriter, State, render_item
from agent_protocol import Decision, ProviderError, ToolCall, empty_selection, strict_json
from eval.scripted_scenarios import MARKET_CHANGE, ScriptedProvider
from providers import OpenAIResponsesProvider, ENDPOINT
from scripts.check_cp4_trace import check_trace

ROOT = Path(__file__).resolve().parents[1]
KEY = "TEST_ONLY_SECRET_DO_NOT_LOG"


def answer(items=None, kind="answer", clarification=None, selection=None):
    return {"kind": kind, "items": items or [], "clarification": clarification,
            "follow_up": None, "selection": selection or empty_selection()}


def call(name, arguments, call_id="call_one"):
    return Decision(call=ToolCall(call_id, name, json.dumps(arguments)))


def final_fact(fact_id="F01"):
    return Decision(answer=answer([{"kind": "fact", "ref": fact_id}]))


class QueueProvider:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.contexts, self.observations, self.messages = [], [], []

    def public_config(self):
        return {"provider": "test_double", "requested_model": None, "live_model": False}

    def start_turn(self, context, instructions):
        self.contexts.append(copy.deepcopy(context))
        self.instructions = instructions

    def next_step(self):
        if not self.decisions: raise ProviderError("TEST_SEQUENCE_EXHAUSTED")
        value = self.decisions.pop(0)
        if isinstance(value, Exception): raise value
        return value

    def observe(self, call_id, observation):
        self.observations.append(observation)

    def feedback(self, message):
        self.messages.append(message)


class AgentLoopTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        manifest = json.loads((ROOT / "inputs/manifest.json").read_text())
        for name, expected in manifest["sha256"].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                raise AssertionError(f"Evidence input changed: {name}")

    def test_scripted_four_turns_use_tools_and_preserve_scope(self):
        provider = ScriptedProvider()
        instance = EvidenceAgent(provider)
        results = [instance.ask(turn["prompt"]) for turn in MARKET_CHANGE]
        self.assertEqual([r["status"] for r in results], ["answered"] * 4)
        self.assertIn("-0.2 percentage points", results[0]["answer"])
        self.assertIn("-20 basis points", results[0]["answer"])
        self.assertIn("9.1%", results[0]["answer"])
        self.assertEqual(results[1]["state_after"]["periods"], results[0]["state_after"]["periods"])
        self.assertEqual(results[1]["state_after"]["geographies"], ["City Core", "City Fringe"])
        self.assertEqual([c["fact_ids"] for c in results[1]["claims"]], [["F03", "F04"], ["F05", "F06"]])
        self.assertEqual(len(results[2]["claims"]), 2)
        self.assertEqual(results[2]["claims"], results[1]["claims"])
        self.assertEqual(len(instance.ledger.observations), 9)
        self.assertIn("not zero market activity", results[3]["answer"])

    def test_new_session_clears_state_evidence_and_previous_references(self):
        provider = QueueProvider([call("get_fact", {"fact_id": "F01"}), final_fact()])
        instance = EvidenceAgent(provider)
        self.assertEqual(instance.ask("Show F01")["status"], "answered")
        previous_session = instance.session_id
        instance.new_session()
        self.assertNotEqual(instance.session_id, previous_session)
        self.assertEqual(asdict(instance.state), asdict(State()))
        self.assertEqual(instance.ledger.facts, {})
        provider.decisions = [final_fact()]
        instance.max_steps = 1
        self.assertEqual(instance.ask("What about there?")["status"], "budget_exhausted")
        self.assertEqual(provider.contexts[-1]["known_evidence"]["facts"], [])

    def test_independent_agent_instances_do_not_share_evidence(self):
        first = EvidenceAgent(QueueProvider([call("get_fact", {"fact_id": "F01"}), final_fact()]))
        first.ask("Show F01")
        second = EvidenceAgent(QueueProvider([]))
        self.assertEqual(second.ledger.facts, {})
        self.assertEqual(second.state.evidence_ids, [])

    def test_clarification_retains_periods_and_multiple_places(self):
        provider = QueueProvider([Decision(answer=answer(kind="clarify", clarification="referent"))])
        instance = EvidenceAgent(provider)
        instance.state.geographies = ["City Core", "City Fringe"]
        instance.state.periods = [{"start": "2026-01-01", "end": "2026-06-30"}]
        result = instance.ask("What about there?")
        self.assertEqual(result["status"], "clarified")
        self.assertEqual(result["state_after"], result["state_before"])
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["answer"], "Which geography or asset do you mean?")

    def test_invented_ids_are_not_dispatched_then_corrected_by_search(self):
        provider = QueueProvider([call("get_fact", {"fact_id": "F01"}, "invented"),
            call("search_facts", {"topic": "vacancy", "geography": "Central London"}, "discover"), final_fact()])
        instance = EvidenceAgent(provider)
        result = instance.ask("Show the Central London vacancy evidence")
        self.assertEqual(result["status"], "answered")
        self.assertFalse(provider.observations[0]["executed"])
        self.assertEqual(provider.observations[0]["arguments"], {"fact_id": "F01"})
        self.assertEqual(provider.observations[0]["result"]["error"]["code"], "INVALID_ARGUMENT")
        self.assertTrue(provider.observations[1]["executed"])

    def test_ids_returned_by_coverage_and_sources_can_be_retrieved(self):
        for first in [call("coverage_gap", {"request": "City Core vacancy in Q2 2026"}, "discover"),
                      call("get_source", {"source_id": "S02"}, "discover")]:
            with self.subTest(first=first.call.name):
                provider = QueueProvider([first, call("get_fact", {"fact_id": "F04"}, "retrieve"), final_fact("F04")])
                result = EvidenceAgent(provider).ask("Inspect S02 and the City Core evidence")
                self.assertEqual(result["status"], "answered")
                self.assertTrue(provider.observations[1]["executed"])

    def test_unknown_explicit_id_returns_error_not_a_fact(self):
        provider = QueueProvider([call("get_fact", {"fact_id": "F999"}),
                                  Decision(answer=answer(kind="clarify", clarification="scope"))])
        result = EvidenceAgent(provider).ask("Show F999")
        self.assertEqual(provider.observations[0]["result"]["error"]["code"], "NOT_FOUND")
        self.assertEqual(result["status"], "clarified")
        self.assertEqual(result["claims"], [])

    def test_unobserved_answer_ref_is_rejected_and_cannot_publish(self):
        for item in [{"kind": "fact", "ref": "F99"}, {"kind": "comparison", "ref": "T1.O1"},
                     {"kind": "source", "ref": "S17"}, {"kind": "coverage", "ref": "missing"}]:
            with self.subTest(item=item):
                provider = QueueProvider([Decision(answer=answer([item]))])
                result = EvidenceAgent(provider, max_steps=1).ask("Give me a finding")
                self.assertEqual(result["status"], "budget_exhausted")
                self.assertEqual(result["claims"], [])
                self.assertTrue(provider.messages)

    def test_freeform_numbers_additional_fields_and_duplicate_items_are_rejected(self):
        candidates = [answer([{"kind": "fact", "ref": "F01"}]),
                      {**answer([{"kind": "fact", "ref": "F01"}]), "summary": "Vacancy is 999 percent"},
                      answer([{"kind": "fact", "ref": "F01"}] * 2)]
        candidates[0]["items"][0]["ref"] = "999 percent"
        for candidate in candidates:
            provider = QueueProvider([call("get_fact", {"fact_id": "F01"}), Decision(answer=candidate)])
            result = EvidenceAgent(provider, max_steps=2).ask("Show F01")
            self.assertEqual(result["status"], "budget_exhausted")
            self.assertNotIn("999", result["answer"])

    def test_rejected_answer_can_be_corrected_within_budget(self):
        provider = QueueProvider([call("get_fact", {"fact_id": "F01"}), final_fact("F999"), final_fact()])
        result = EvidenceAgent(provider).ask("Show F01")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(provider.messages), 1)
        self.assertIn("7.4 percent", result["answer"])

    def test_refused_comparison_cannot_be_selected_as_supported(self):
        provider = QueueProvider([call("compare_facts", {"fact_id_a": "F01", "fact_id_b": "F07"}),
            Decision(answer=answer([{"kind": "comparison", "ref": "T1.O1"}]))])
        result = EvidenceAgent(provider, max_steps=2).ask("Compare F01 and F07")
        self.assertEqual(provider.observations[0]["result"]["error"]["code"], "COMPARISON_REFUSED")
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["status"], "budget_exhausted")

    def test_forecast_and_optional_source_qualifications_are_visible(self):
        provider = QueueProvider([call("get_fact", {"fact_id": "F28"}), final_fact("F28")])
        result = EvidenceAgent(provider).ask("Show F28")
        self.assertIn("forecast", result["answer"])
        self.assertIn("2029", result["answer"])
        provider = QueueProvider([call("get_source", {"source_id": "S17"}),
                                 Decision(answer=answer([{"kind": "source", "ref": "S17"}]))])
        result = EvidenceAgent(provider).ask("Inspect S17")
        for qualification in ("bulk csv requires gov.uk one login", "api requires bearer token", "authenticated api not called"):
            self.assertIn(qualification, result["answer"].lower())

    def test_provider_errors_preserve_state_without_fabricated_answer(self):
        provider = QueueProvider([ProviderError("HTTP_429")])
        instance = EvidenceAgent(provider)
        instance.state.geographies = ["City Core"]
        result = instance.ask("What changed?")
        self.assertEqual(result["status"], "provider_error")
        self.assertEqual(result["error_code"], "HTTP_429")
        self.assertEqual(result["state_before"], result["state_after"])
        self.assertEqual(result["claims"], [])

    def test_budget_stops_repeated_calls_and_retains_failure_evidence(self):
        provider = QueueProvider([call("search_facts", {"limit": 1}, str(i)) for i in range(4)])
        result = EvidenceAgent(provider, max_steps=3).ask("Research")
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(len(provider.observations), 3)
        self.assertEqual(result["claims"], [])

    def test_duplicate_call_ids_fail_before_duplicate_execution(self):
        provider = QueueProvider([call("search_facts", {"limit": 1}), call("search_facts", {"limit": 1})])
        result = EvidenceAgent(provider).ask("Research")
        self.assertEqual(result["error_code"], "DUPLICATE_OR_INVALID_CALL_ID")
        self.assertEqual(len(provider.observations), 1)

    def test_malformed_tool_json_and_extra_sql_are_errors(self):
        for decision in [Decision(call=ToolCall("id", "search_facts", '{"limit":1,"limit":2}')),
                         Decision(call=ToolCall("id", "search_facts", '{"limit":1e999}')),
                         call("search_facts", {"sql": "SELECT 1"})]:
            provider = QueueProvider([decision, Decision(answer=answer(kind="clarify", clarification="scope"))])
            result = EvidenceAgent(provider).ask("Research")
            self.assertEqual(provider.observations[0]["result"]["error"]["code"], "INVALID_ARGUMENT")
            if provider.observations[0]["arguments"] is None:
                self.assertEqual(provider.observations[0]["rejected_raw_arguments"], decision.call.arguments)
            self.assertEqual(result["status"], "clarified")

    def test_invalid_period_patch_is_atomic_and_clarification_cannot_change_selection(self):
        for candidate in [answer([{"kind": "fact", "ref": "F01"}], selection={**empty_selection(), "periods": [{"start": "2026-02-30", "end": "2026-06-30"}]}),
                          answer(kind="clarify", clarification="referent", selection={**empty_selection(), "geographies": ["other"]})]:
            provider = QueueProvider([call("get_fact", {"fact_id": "F01"}), Decision(answer=candidate)])
            instance = EvidenceAgent(provider, max_steps=2)
            instance.state.geographies = ["City Core"]
            result = instance.ask("Show F01")
            self.assertEqual(result["status"], "budget_exhausted")
            self.assertEqual(result["state_before"], result["state_after"])

    def test_live_path_does_not_load_fixture_json_or_expected_answers(self):
        real_read = Path.read_text
        forbidden = {"facts.json", "sources.json", "coverage.json", "chat_cases.json", "golden-cases.json"}
        def guarded(path, *args, **kwargs):
            if path.name in forbidden: raise AssertionError("Oracle/fixture JSON loaded as runtime evidence")
            return real_read(path, *args, **kwargs)
        provider = QueueProvider([call("search_facts", {"topic": "vacancy", "geography": "Central London"}), final_fact()])
        with patch.object(Path, "read_text", guarded):
            result = EvidenceAgent(provider).ask("Show Central London vacancy")
        self.assertEqual(result["status"], "answered")
        self.assertNotIn("take_up_relative_growth_approx_percent", provider.instructions)
        self.assertEqual(provider.contexts[0]["known_evidence"]["facts"], [])

    def test_message_and_step_limits_are_validated(self):
        for steps in [0, 21, True, 1.5]:
            with self.assertRaises(ValueError): EvidenceAgent(QueueProvider([]), max_steps=steps)
        instance = EvidenceAgent(QueueProvider([]))
        for message in ["", " ", "x" * 4001, None]:
            with self.assertRaises(ValueError): instance.ask(message)
        with self.assertRaises(ValueError): strict_json("[" * 2000 + "]" * 2000)
        provider = QueueProvider([Decision(answer={"untrusted_number": float("inf")})])
        self.assertEqual(EvidenceAgent(provider).ask("Inspect")["error_code"], "INVALID_DECISION_JSON")


def wire_response(output, **overrides):
    return {"id": "resp_mock", "model": "mock-snapshot", "status": "completed", "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, **overrides}


def wire_answer(payload):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(payload)}]}


class ProviderTests(unittest.TestCase):
    def provider(self):
        return OpenAIResponsesProvider("model-set-locally", KEY)

    def test_stateless_function_loop_replays_reasoning_and_matching_call_output(self):
        provider = self.provider()
        reasoning = {"type": "reasoning", "id": "rs_mock", "summary": [], "encrypted_content": "OPAQUE_REASONING"}
        first = wire_response([reasoning, {"type": "function_call", "call_id": "call_search", "name": "search_facts",
            "arguments": json.dumps({"topic": "vacancy", "geography": "Central London"})}])
        second = wire_response([wire_answer(answer([{"kind": "fact", "ref": "F01"}]))])
        payloads = []
        def transport(payload):
            payloads.append(copy.deepcopy(payload))
            return [first, second][len(payloads) - 1]
        with patch.object(provider, "_post", side_effect=transport):
            result = EvidenceAgent(provider).ask("Show Central London vacancy")
        self.assertEqual(result["status"], "answered")
        self.assertFalse(payloads[0]["store"])
        self.assertFalse(payloads[0]["parallel_tool_calls"])
        self.assertEqual({t["name"] for t in payloads[0]["tools"]}, {"search_facts", "get_fact", "compare_facts", "get_source", "coverage_gap"})
        self.assertTrue(all(t["strict"] is False for t in payloads[0]["tools"]))
        self.assertIn(reasoning, payloads[1]["input"])
        tool_output = next(i for i in payloads[1]["input"] if i.get("type") == "function_call_output")
        self.assertEqual(tool_output["call_id"], "call_search")
        self.assertEqual(json.loads(tool_output["output"])["result"]["data"]["count"], 2)
        self.assertNotIn(KEY, json.dumps(payloads))
        self.assertNotIn("previous_response_id", payloads[1])

    def test_http_request_uses_selected_endpoint_and_does_not_log_key(self):
        provider = self.provider()
        response = io.BytesIO(json.dumps(wire_response([])).encode())
        with patch.object(provider._opener, "open", return_value=response) as opened:
            provider._post({"model": provider.model})
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(opened.call_args.kwargs["timeout"], 45)
        self.assertNotIn(KEY, json.dumps(provider.public_config()))

    def test_endpoint_override_is_loopback_only_and_never_marked_live(self):
        provider = OpenAIResponsesProvider("codex-simulated-model", KEY,
                                           endpoint="http://127.0.0.1:43127/v1/responses")
        self.assertEqual(provider.public_config()["live_model"], False)
        self.assertEqual(provider.public_config()["simulated_endpoint"], True)
        for endpoint in ("http://example.com/v1/responses", "https://example.com/v1/responses",
                         "http://127.0.0.1:43127/other", "http://user:pass@127.0.0.1:43127/v1/responses"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ProviderError):
                OpenAIResponsesProvider("model", KEY, endpoint=endpoint)

    def test_http_and_network_errors_are_sanitized_and_not_retried(self):
        for error, expected in [(HTTPError(ENDPOINT, 401, KEY, {}, io.BytesIO(KEY.encode())), "HTTP_401"),
                                (URLError(KEY), "NETWORK_ERROR")]:
            provider = self.provider()
            with patch.object(provider._opener, "open", side_effect=error) as opened:
                with self.assertRaises(ProviderError) as raised: provider._post({})
            self.assertEqual(str(raised.exception), expected)
            self.assertNotIn(KEY, str(raised.exception))
            self.assertEqual(opened.call_count, 1)
            if isinstance(error, HTTPError): self.assertTrue(error.fp.closed)

    def test_incomplete_refusal_and_malformed_output_fail_closed(self):
        bad = [wire_response([], status="incomplete"), wire_response([], model=None),
               wire_response([], usage=[1]), wire_response("not-list"),
               wire_response([{"type": "message", "content": None}]),
               wire_response([{"type": "message", "content": [{"type": "refusal", "refusal": KEY}]}]),
               wire_response([{"type": "message", "content": [{"type": "output_text", "text": "not-json"}]}])]
        for response in bad:
            with self.subTest(response=response):
                provider = self.provider()
                provider.start_turn({}, "instructions")
                with patch.object(provider, "_post", return_value=response):
                    with self.assertRaises(ProviderError) as raised: provider.next_step()
                self.assertNotIn(KEY, str(raised.exception))

    def test_interim_prose_is_never_published_when_a_tool_is_requested(self):
        provider = self.provider()
        response = wire_response([wire_answer({"unsupported": "999 percent"}),
            {"type": "function_call", "call_id": "c", "name": "search_facts", "arguments": "{}"}])
        provider.start_turn({}, "instructions")
        with patch.object(provider, "_post", return_value=response): decision = provider.next_step()
        self.assertIsNotNone(decision.call)
        self.assertIsNone(decision.answer)
        self.assertTrue(decision.metadata["interim_text_ignored"])

    def test_multiple_calls_and_wrong_callback_id_are_rejected(self):
        item = {"type": "function_call", "call_id": "a", "name": "search_facts", "arguments": "{}"}
        provider = self.provider()
        provider.start_turn({}, "instructions")
        with patch.object(provider, "_post", return_value=wire_response([item, {**item, "call_id": "b"}])):
            with self.assertRaises(ProviderError): provider.next_step()
        provider = self.provider()
        provider.start_turn({}, "instructions")
        with patch.object(provider, "_post", return_value=wire_response([item])): provider.next_step()
        with self.assertRaises(ProviderError): provider.observe("wrong", {})

    def test_provider_configuration_and_context_limits(self):
        for model, key in [("", KEY), ("model", "")]:
            with self.assertRaises(ProviderError): OpenAIResponsesProvider(model, key)
        with self.assertRaises(ProviderError): OpenAIResponsesProvider("model", KEY, timeout=61)
        with self.assertRaises(ProviderError): self.provider()._post({"input": "x" * 1_000_001})


class TraceAndCLITests(unittest.TestCase):
    def test_trace_records_provenance_state_tools_claims_and_failure_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            provider = QueueProvider([call("get_fact", {"fact_id": "F01"}), final_fact("F99"), final_fact()])
            trace = TraceWriter(path, provider.public_config())
            instance = EvidenceAgent(provider, trace)
            instance.ask("Show F01")
            trace.close()
            events = [json.loads(line) for line in path.read_text().splitlines()]
        header = events[0]
        self.assertEqual(header["fixture_manifest_sha256"], hashlib.sha256((ROOT / "inputs/manifest.json").read_bytes()).hexdigest())
        self.assertIn("agent_skills/CORE.md", header["code_sha256"])
        self.assertTrue(any(e["event"] == "answer_rejected" for e in events))
        self.assertTrue(any(e["event"] == "tool_result" for e in events))
        end = events[-1]
        self.assertEqual(end["event"], "turn_end")
        self.assertEqual(end["claims"][0]["fact_ids"], ["F01"])
        self.assertEqual(end["claims"][0]["scopes"][0]["geography"], "Central London")

    def test_trace_never_overwrites_and_cannot_write_inside_frozen_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            path.write_text("preserve")
            with self.assertRaises(FileExistsError): TraceWriter(path, {})
            self.assertEqual(path.read_text(), "preserve")
        with self.assertRaises(ValueError): TraceWriter(ROOT / "inputs/new.jsonl", {})
        self.assertFalse((ROOT / "inputs/new.jsonl").exists())

    def test_trace_checker_requeries_fixture_and_rejects_unreferenced_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            provider = QueueProvider([call("get_fact", {"fact_id": "F01"}), final_fact()])
            trace = TraceWriter(path, provider.public_config())
            EvidenceAgent(provider, trace).ask("Show F01")
            trace.close()
            self.assertEqual(check_trace(path)["answered"], 1)
            original = [json.loads(line) for line in path.read_text().splitlines()]
            changed = copy.deepcopy(original)
            next(e for e in changed if e["event"] == "tool_result")["result"]["data"]["value_num"] = 999
            path.write_text("\n".join(json.dumps(e) for e in changed) + "\n")
            with self.assertRaisesRegex(ValueError, "differs from the current preserved fixture"): check_trace(path)
            changed = copy.deepcopy(original)
            changed[-1]["final_answer"] += " Unsupported vacancy 999%."
            path.write_text("\n".join(json.dumps(e) for e in changed) + "\n")
            with self.assertRaisesRegex(ValueError, "Unreferenced final text"): check_trace(path)

    def test_trace_checker_rejects_empty_incomplete_and_offline_as_live(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            provider = QueueProvider([Decision(answer=answer(kind="clarify", clarification="scope"))])
            trace = TraceWriter(path, provider.public_config())
            EvidenceAgent(provider, trace).ask("What scope?")
            trace.close()
            self.assertEqual(check_trace(path)["clarified"], 1)
            with self.assertRaisesRegex(ValueError, "not a supported live mode"): check_trace(path, require_live=True)
            lines = path.read_text().splitlines()
            path.write_text("\n".join(lines[:-1]) + "\n")
            with self.assertRaisesRegex(ValueError, "Incomplete or empty"): check_trace(path)

    def test_trace_omits_credentials_and_raw_reasoning(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            provider = OpenAIResponsesProvider("local-model", KEY)
            trace = TraceWriter(path, provider.public_config())
            response = wire_response([{"type": "reasoning", "summary": [], "encrypted_content": "OPAQUE_REASONING"},
                wire_answer(answer(kind="clarify", clarification="scope"))])
            with patch.object(provider, "_post", return_value=response): EvidenceAgent(provider, trace).ask("Please clarify")
            trace.close()
            text = path.read_text()
        self.assertNotIn(KEY, text)
        self.assertNotIn("OPAQUE_REASONING", text)
        self.assertIn("mock-snapshot", text)

    def test_environment_is_literal_and_os_values_take_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text('MODEL_PROVIDER=openai\nMODEL_NAME="$(do-not-execute)"\nMODEL_API_KEY=local\n')
            with patch.dict(os.environ, {"MODEL_API_KEY": "from-environment"}, clear=True):
                result = agent.load_environment(path)
            self.assertEqual(result["MODEL_NAME"], "$(do-not-execute)")
            self.assertEqual(result["MODEL_API_KEY"], "from-environment")
            for content in ['UNKNOWN_KEY=secret', 'MODEL_NAME=one\nMODEL_NAME=two', 'MODEL_NAME="unclosed']:
                path.write_text(content)
                with self.assertRaises(ValueError): agent.load_environment(path)

    def test_missing_credentials_do_not_fall_back_to_scripted_or_make_a_request(self):
        with patch.object(agent, "load_environment", return_value={}), patch.object(OpenAIResponsesProvider, "_post") as network:
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                code = agent.main(["--provider", "openai", "--message", "Hello"])
        self.assertEqual(code, 2)
        self.assertIn("MODEL_NAME_REQUIRED", err.getvalue())
        self.assertNotIn("OFFLINE", out.getvalue())
        network.assert_not_called()

    def test_scripted_requires_explicit_case_and_has_no_live_label(self):
        with patch.object(agent, "load_environment", return_value={}):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(agent.main(["--provider", "scripted", "--interactive"]), 2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "offline.jsonl"
            result = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--provider", "scripted", "--case", "market_change", "--trace", str(path)],
                                    cwd=temporary, capture_output=True, text=True)
            events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NOT A MODEL EVALUATION", result.stdout)
        self.assertIs(events[0]["provider"]["live_model"], False)

    def test_interactive_state_new_and_quit_commands(self):
        provider = QueueProvider([Decision(answer=answer(kind="clarify", clarification="scope"))])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "interactive.jsonl"
            with patch.object(agent, "load_environment", return_value={"MODEL_NAME": "test", "MODEL_API_KEY": KEY}), \
                 patch.object(agent, "OpenAIResponsesProvider", return_value=provider), \
                 patch("builtins.input", side_effect=["What about there?", "/state", "/new", "/quit"]), \
                 redirect_stdout(io.StringIO()) as output:
                provider.model = "test"
                code = agent.main(["--provider", "openai", "--interactive", "--trace", str(path)])
            events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(code, 0)
        self.assertIn("New conversation", output.getvalue())
        self.assertEqual(len([e for e in events if e["event"] == "session_start"]), 2)
        self.assertNotIn(KEY, output.getvalue())

    def test_interactive_failure_exit_survives_new_session_and_later_success(self):
        provider = QueueProvider([ProviderError("TEST_PROVIDER_FAILURE"),
                                  Decision(answer=answer(kind="clarify", clarification="scope"))])
        provider.model = "test"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "failed-then-recovered.jsonl"
            with patch.object(agent, "load_environment", return_value={"MODEL_NAME": "test", "MODEL_API_KEY": KEY}), \
                 patch.object(agent, "OpenAIResponsesProvider", return_value=provider), \
                 patch("builtins.input", side_effect=["First request", "/new", "Next request", "/quit"]), \
                 redirect_stdout(io.StringIO()):
                code = agent.main(["--provider", "openai", "--interactive", "--trace", str(path)])
            report = check_trace(path)
        self.assertEqual(code, 1)
        self.assertEqual((report["errors"], report["clarified"], report["sessions"]), (1, 1, 2))



class SearchCompletionRegressionTests(unittest.TestCase):
    def test_larger_search_after_truncation_is_executed(self):
        instance=EvidenceAgent(QueueProvider([]))
        first=instance._execute(call('search_facts',{'limit':1}).call,'Research','first')
        self.assertTrue(first['result']['data']['truncated'])
        larger=instance._execute(call('search_facts',{'limit':100}).call,'Research','larger')
        self.assertTrue(larger['executed'])
        self.assertEqual(larger['result']['data']['count'],39)
        again=instance._execute(call('search_facts',{'limit':100}).call,'Research','again')
        self.assertFalse(again['executed'])
        self.assertEqual(again['result']['error']['code'],'INVALID_ARGUMENT')

    def test_invalid_limit_after_truncation_is_an_envelope_not_exception(self):
        instance=EvidenceAgent(QueueProvider([]))
        instance._execute(call('search_facts',{'limit':1}).call,'Research','first')
        invalid=instance._execute(call('search_facts',{'limit':'all'}).call,'Research','invalid')
        self.assertFalse(invalid['executed'])
        self.assertEqual(invalid['result']['error']['code'],'INVALID_ARGUMENT')

    def test_same_truncated_search_is_not_reexecuted(self):
        instance=EvidenceAgent(QueueProvider([]))
        instance._execute(call('search_facts',{'limit':1}).call,'Research','first')
        again=instance._execute(call('search_facts',{'limit':1}).call,'Research','again')
        self.assertFalse(again['executed'])

class SourceObservationPresentationTests(unittest.TestCase):
    def test_project_completion_preserves_fit_out_observation(self):
        import agent_tools
        from agent_loop import Ledger
        ledger=Ledger()
        ledger.ingest({'observation_id':'completion','tool':'get_fact','result':agent_tools.invoke('get_fact',{'fact_id':'F26'})})
        text=render_item({'kind':'fact','ref':'F26'},ledger)['text']
        self.assertIn('tenant fit-out to follow',text)
        self.assertIn('not newly available speculative supply',text)

class ProviderDiagnosticTraceTests(unittest.TestCase):
    def test_rejected_worker_details_survive_without_provider_step(self):
        error=ProviderError('APPLE_INVALID_DECISION_REFERENCES',diagnostic={'rule':'scope','task':'brief','minimum':2,'selected_count':2,'allowed_count':4,'selected_supported_references':['fact:F33','fact:F34'],'raw_model_output':'DO_NOT_LOG'})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'diagnostic.jsonl'
            trace=TraceWriter(path,{'provider':'scripted','live_model':False})
            instance=EvidenceAgent(QueueProvider([error]),trace)
            result=instance.ask('Inspect evidence');trace.close()
            events=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(result['status'],'provider_error')
            self.assertEqual(events[-1]['provider_diagnostic']['rule'],'scope')
            self.assertFalse(any(e['event']=='provider_step' for e in events))
            self.assertNotIn('DO_NOT_LOG',path.read_text())
            self.assertEqual(check_trace(path)['errors'],1)

if __name__ == "__main__": unittest.main()
