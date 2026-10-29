"""Synthetic semantic-checker regressions, not a model evaluation."""
import copy
import unittest

from scripts.check_cp4_live_review import PROMPTS, check_semantics, check_lookup_semantics, FACT_LOOKUP_PROMPT, FACT_LOOKUP_PROMPTS, VALUE_FOLLOW_UP_PROMPTS


def sample():
    empty = dict(topics=[], geographies=[], periods=[], entity=None, evidence_ids=[], previous_answer_items=[])
    events = [{"event": "session_start", "session_id": s} for s in ("A", "B")]
    def comparison(ids, place):
        return dict(kind="comparison", fact_ids=ids, scopes=[{"geography": place}], source_ids=["S01", "S02"])
    core = comparison(["F03", "F04"], "City Core")
    fringe = comparison(["F05", "F06"], "City Fringe")
    claims = [[comparison(["F01", "F02"], "Central London")], [core, fringe], [core, fringe], [],
              [dict(kind="coverage", fact_ids=[], ref="T5.O2")], [],
              [dict(kind="source", fact_ids=[], source_ids=["S17"], text="GOV.UK One Login; bearer token; authenticated API not called")]]
    for i, prompt in enumerate(PROMPTS):
        session = "A" if i < 5 else "B"
        state = copy.deepcopy(empty)
        if i < 4:
            state.update(geographies=["Central London"] if i == 0 else ["City Core", "City Fringe"], periods=[{"start": "2026-01-01", "end": "2026-06-30"}])
        events.append(dict(event="turn_start", session_id=session, turn_id=str(i), user_message=prompt, state_before=copy.deepcopy(state)))
        events.append(dict(event="turn_end", session_id=session, turn_id=str(i), status="clarified" if i in (3,5) else "answered", final_answer="Which geography or asset do you mean?" if i in (3,5) else "", claims=copy.deepcopy(claims[i]), state_after=state))
    events.append(dict(event="tool_result", session_id="A", observation_id="T5.O2", tool="coverage_gap", result={"ok": True, "data": {
        "assessment": "no_matching_facts_in_fixture", "available_count": 0, "requested_scope": {"geography": "Canary Wharf", "topic": "vacancy", "period_start": "2026-04-01", "period_end": "2026-06-30"}}}))
    return events


class LiveReviewTests(unittest.TestCase):
    def test_complete_sequence_and_source_spelling(self):
        self.assertEqual(check_semantics(sample())["full_manual_sequence"], "PASS_AUTOMATED_SEMANTIC_CHECKS")

    def test_cited_but_wrong_central_london_reuse_fails(self):
        events = sample()
        ends = [e for e in events if e["event"] == "turn_end"]
        ends[1]["claims"] = copy.deepcopy(ends[0]["claims"])
        with self.assertRaisesRegex(ValueError, "Missing relevant comparison"):
            check_semantics(events)

    def test_missing_prompt_or_wrong_gap_period_fails(self):
        events = sample()
        with self.assertRaisesRegex(ValueError, "seven model prompts"):
            check_semantics([e for e in events if e.get("user_message") != PROMPTS[-1]])
        events[-1]["result"]["data"]["requested_scope"]["period_start"] = "2026-01-01"
        with self.assertRaisesRegex(ValueError, "Gap scope"):
            check_semantics(events)

    def test_supplementary_lookup_checks_fact_ids_and_single_place_continuity(self):
        def stream(prompts, fact_id, source_id, tool):
            state = {"geographies": ["Central London"] if fact_id == "F01" else ["City Core"], "periods": [{"start": "2026-03-31", "end": "2026-03-31"}] if fact_id == "F01" else [{"start": "2026-04-01", "end": "2026-06-30"}]}
            events = [{"event": "session_start"}]
            for i, prompt in enumerate(prompts):
                events += [{"event": "turn_start", "user_message": prompt, "turn_id": str(i), "state_before": state},
                           {"event": "turn_end", "status": "answered", "state_after": state, "claims": [{"kind": "fact", "fact_ids": [fact_id], "source_ids": [source_id]}]}]
            events.append({"event": "tool_result", "tool": tool, "turn_id": "0", "arguments": {"fact_id": "F01"}, "result": {"ok": True}})
            return events
        facts = stream(FACT_LOOKUP_PROMPTS, "F01", "S01", "get_fact")
        values = stream(VALUE_FOLLOW_UP_PROMPTS, "F04", "S02", "search_facts")
        self.assertEqual(check_lookup_semantics(facts, values)["supplementary_turns"], 4)
        ends = [e for e in values if e["event"] == "turn_end"]
        ends[-1]["claims"][0]["fact_ids"] = ["F01"]
        with self.assertRaisesRegex(ValueError, "wrong fact/source"):
            check_lookup_semantics(facts, values)

    def test_source_without_access_limitations_fails(self):
        events = sample()
        ends = [e for e in events if e["event"] == "turn_end"]
        ends[-1]["claims"][0]["text"] = "S17 source exists"
        with self.assertRaisesRegex(ValueError, "qualifications missing"):
            check_semantics(events)
