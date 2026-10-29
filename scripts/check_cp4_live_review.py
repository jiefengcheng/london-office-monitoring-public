#!/usr/bin/env python3
"""Check the complete CP4 manual sequence; never imported by the live agent."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_cp4_trace import check_trace, require
from agent_protocol import strict_json
from agent_loop import CLARIFICATIONS

PROMPTS = [
    "What changed in Central London between Q1 and Q2 2026?",
    "Focus on City Core and City Fringe. Did they move in the same direction?",
    "Give me two cited points for our meeting.",
    "What about there?",
    "Canary Wharf vacancy in Q2 2026",
    "What about there?",
    "Show source S17 and its access qualifications.",
]


def check_semantics(events):
    starts = [e for e in events if e["event"] == "turn_start"]
    ends = [e for e in events if e["event"] == "turn_end"]
    sessions = [e for e in events if e["event"] == "session_start"]
    require([e["user_message"] for e in starts] == PROMPTS, "Expected all seven model prompts in the ten-input manual sequence")
    require(len(ends) == 7 and len(sessions) == 2, "Expected seven completed model turns and two sessions")
    require([e["status"] for e in ends] == ["answered", "answered", "answered", "clarified", "answered", "clarified", "answered"], "Unexpected answer/clarification sequence")
    require(all((a["session_id"], a["turn_id"]) == (b["session_id"], b["turn_id"]) for a,b in zip(starts, ends)), "Turn order mismatch")
    require(len({e["session_id"] for e in starts[:5]}) == 1 and starts[5]["session_id"] == starts[6]["session_id"] != starts[0]["session_id"], "Reset at wrong point")
    require(not any(e["event"] == "answer_rejected" for e in events), "Answer rejection in review run")
    require(all(e["result"]["ok"] for e in events if e["event"] == "tool_result"), "Unresolved tool error in review run")
    def comparisons(index, required_pairs, geographies):
        claims = ends[index]["claims"]
        pairs = {tuple(c["fact_ids"]) for c in claims if c["kind"] == "comparison"}
        require(required_pairs <= pairs, "Missing relevant comparison on turn " + str(index+1))
        require(all(c["kind"] in ("comparison", "coverage") and all(s["geography"] in geographies for s in c["scopes"]) for c in claims), "Wrong geography or claim kind on turn " + str(index+1))
        require(set(ends[index]["state_after"]["geographies"]) == geographies, "Selection scope mismatch")
        require(ends[index]["state_after"]["periods"] in ([{"start": "2026-01-01", "end": "2026-06-30"}], [{"start": "2026-01-01", "end": "2026-03-31"}, {"start": "2026-04-01", "end": "2026-06-30"}]), "Q1/Q2 window not retained")
    comparisons(0, {("F01", "F02")}, {"Central London"})
    comparisons(1, {("F03", "F04"), ("F05", "F06")}, {"City Core", "City Fringe"})
    comparisons(2, {("F03", "F04"), ("F05", "F06")}, {"City Core", "City Fringe"})
    require(len(ends[2]["claims"]) == 2, "Meeting briefing must contain exactly two relevant points")
    require(all(ends[i].get("base_answer", ends[i].get("final_answer")) == CLARIFICATIONS["referent"] for i in (3, 5)), "Ambiguous referent requires a referent clarification")
    require(ends[3]["state_after"] == starts[3]["state_before"], "Ambiguous referent changed scope")
    gap = ends[4]["claims"]
    require(len(gap) == 1 and gap[0]["kind"] == "coverage" and not gap[0]["fact_ids"], "Expected fixture-gap answer")
    observation = next((e for e in events if e["event"] == "tool_result" and e["session_id"] == ends[4]["session_id"] and e["observation_id"] == gap[0]["ref"]), None)
    require(observation is not None and observation["tool"] == "coverage_gap", "Gap observation missing")
    data = observation["result"]["data"]
    scope = data["requested_scope"]
    require(data["assessment"] == "no_matching_facts_in_fixture" and data["available_count"] == 0, "Gap assessment not fixture-limited")
    require(scope.get("geography") == "Canary Wharf" and scope.get("topic") == "vacancy" and scope.get("period_start") == "2026-04-01" and scope.get("period_end") == "2026-06-30", "Gap scope mismatch")
    empty = {"topics": [], "geographies": [], "periods": [], "entity": None, "evidence_ids": [], "previous_answer_items": []}
    require(starts[5]["state_before"] == ends[5]["state_after"] == empty, "Reset retained prior scope/evidence")
    source = ends[6]["claims"]
    require(len(source) == 1 and source[0]["kind"] == "source" and source[0]["source_ids"] == ["S17"] and not source[0]["fact_ids"], "Expected S17 source qualification only")
    text = re.sub(r"\s+", "", source[0]["text"].casefold())
    require(all(word in text for word in ("onelogin", "token", "notcalled")), "Source access qualifications missing")
    return {"full_manual_sequence": "PASS_AUTOMATED_SEMANTIC_CHECKS", "model_turns": 7, "cli_inputs": 10,
            "local_handoff_coverage": "CORE_ONLY; supplementary lookup traces required for the complete local handoff",
            "independent_human_acceptance": "PENDING_USER", "scope": "CP4 manual sequence only; not CP5 or release"}



FACT_LOOKUP_PROMPT = "Show fact F01 and its original source."
FACT_LOOKUP_PROMPTS = [FACT_LOOKUP_PROMPT, "What about there?"]
VALUE_FOLLOW_UP_PROMPTS = ["What is City Core vacancy in Q2 2026?", "What about there?"]


def check_lookup_semantics(fact_events, value_events):
    """Supplement the original sequence with fresh-session/default-route checks."""
    def turns(events, prompts):
        starts = [e for e in events if e["event"] == "turn_start"]
        ends = [e for e in events if e["event"] == "turn_end"]
        require([e["user_message"] for e in starts] == prompts, "Supplementary lookup prompts mismatch")
        require(len(ends) == len(prompts) and all(e["status"] == "answered" for e in ends), "Supplementary lookup did not answer every turn")
        require(len([e for e in events if e["event"] == "session_start"]) == 1, "Supplementary lookup must use a fresh single session")
        require(not any(e["event"] == "answer_rejected" for e in events), "Rejected supplementary answer")
        for end in ends:
            require(len(end["claims"]) == 1 and end["claims"][0]["kind"] == "fact", "Supplementary lookup must publish one fact")
        return starts, ends, [e for e in events if e["event"] == "tool_result"]
    fact_starts, facts, fact_tools = turns(fact_events, FACT_LOOKUP_PROMPTS)
    for end in facts:
        require(end["claims"][0]["fact_ids"] == ["F01"] and end["claims"][0]["source_ids"] == ["S01"], "Direct lookup returned wrong fact/source")
        require(end["state_after"]["geographies"] == ["Central London"] and end["state_after"]["periods"] == [{"start": "2026-03-31", "end": "2026-03-31"}], "Direct ID lookup/follow-up lost observed scope")
    require(fact_starts[1]["state_before"] == facts[0]["state_after"], "Direct ID follow-up state discontinuity")
    require(len(fact_tools) == 1 and fact_tools[0]["tool"] == "get_fact" and fact_tools[0]["arguments"] == {"fact_id": "F01"}, "Direct lookup must use get_fact once, without repeated searches")
    starts, values, value_tools = turns(value_events, VALUE_FOLLOW_UP_PROMPTS)
    for end in values:
        require(end["claims"][0]["fact_ids"] == ["F04"] and end["claims"][0]["source_ids"] == ["S02"], "Single-quarter lookup returned wrong fact/source")
        require(end["state_after"]["geographies"] == ["City Core"] and end["state_after"]["periods"] == [{"start": "2026-04-01", "end": "2026-06-30"}], "Single-place follow-up lost geography/quarter")
    require(starts[1]["state_before"] == values[0]["state_after"], "Single-place follow-up state discontinuity")
    require(len(value_tools) == 1 and value_tools[0]["tool"] == "search_facts" and value_tools[0]["turn_id"] == starts[0]["turn_id"], "Single-place follow-up should reuse the observed fact")
    require(all(e["result"]["ok"] for e in fact_tools + value_tools), "Supplementary tool failed")
    return {"supplementary_lookups": "PASS_DIRECT_FACT_AND_SINGLE_PLACE_CONTINUITY", "supplementary_turns": 4, "supplementary_tool_calls": 2}


def read_events(path):
    return [strict_json(line, max_chars=4_000_000) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--expect-provider", choices=["apple", "openai"], required=True)
    parser.add_argument("--fact-lookup", type=Path, help="Fresh-session default-provider direct fact trace")
    parser.add_argument("--value-follow-up", type=Path, help="Fresh-session default-provider single-place lookup/follow-up trace")
    args = parser.parse_args()
    if bool(args.fact_lookup) != bool(args.value_follow_up):
        parser.error("--fact-lookup and --value-follow-up must be supplied together")
    try:
        result = check_trace(args.trace, require_live=True, expect_provider=args.expect_provider)
        events = read_events(args.trace)
        result.update(check_semantics(events))
        if args.fact_lookup:
            extra = []
            for path in (args.fact_lookup, args.value_follow_up):
                check_trace(path, require_live=True, expect_provider=args.expect_provider)
                stream = read_events(path)
                for key in ("source_commit", "code_sha256", "fixture_manifest_sha256"):
                    require(stream[0][key] == events[0][key], "Supplementary source/fixture identity differs")
                for key in ("os_version", "sdk_library_sha256"):
                    require(stream[0]["provider"].get("system_model", {}).get(key) == events[0]["provider"].get("system_model", {}).get(key), "Supplementary model environment differs")
                extra.append(stream)
            result.update(check_lookup_semantics(*extra))
            tools = {e["tool"] for stream in (events, *extra) for e in stream if e["event"] == "tool_result"}
            require(tools == {"search_facts", "get_fact", "compare_facts", "get_source", "coverage_gap"}, "Not all five gateway tools were exercised live")
            result["all_five_tools_exercised_live"] = True
            result["total_live_turns"] = result["turns"] + result["supplementary_turns"]
            result["total_live_tool_calls"] = result["tool_calls"] + result["supplementary_tool_calls"]
            result["local_handoff_coverage"] = "PASS_ALL_FIVE_TOOLS_AND_SUPPLEMENTARY_LOOKUPS"
            result["scope"] = "CP4 local core and supplementary lookup checks; not independent human acceptance, CP5 or release"
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print("FAIL | CP4 full live review | " + str(error), file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
