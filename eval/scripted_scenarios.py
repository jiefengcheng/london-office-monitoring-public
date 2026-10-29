"""Explicit offline tool plans. Never imported by the live provider/agent loop.

These exercise mechanics, not intent inference or model factual accuracy. No
answer values or original chat-case expected fields are supplied to a provider.
"""
import copy
import json

from agent_protocol import Decision, ProviderError, ToolCall, empty_selection

PERIODS = [{"start": "2026-01-01", "end": "2026-03-31"},
           {"start": "2026-04-01", "end": "2026-06-30"}]


def search(topic, geography):
    return ("search_facts", {"topic": topic, "geography": geography,
                             "period_start": "2026-01-01", "period_end": "2026-06-30", "limit": 100})


def compare(a, b):
    return ("compare_facts", {"fact_id_a": a, "fact_id_b": b})


MARKET_CHANGE = [
    {"prompt": "What changed in Central London between Q1 and Q2 2026?",
     "calls": [search("vacancy", "Central London"), compare("F01", "F02"),
               search("leasing", "Central London"), compare("F07", "F08")],
     "selection": {"topics": ["vacancy", "leasing"], "geographies": ["Central London"], "periods": PERIODS, "entity": None}},
    {"prompt": "Focus on City Core and City Fringe. Did they move in the same direction?",
     "calls": [search("vacancy", "City Core"), compare("F03", "F04"),
               search("vacancy", "City Fringe"), compare("F05", "F06")],
     "selection": {"topics": ["vacancy"], "geographies": ["City Core", "City Fringe"], "periods": None, "entity": None}},
    {"prompt": "Give me two cited points for our meeting.", "calls": [], "use_previous": True,
     "selection": empty_selection()},
    {"prompt": "Canary Wharf vacancy in Q2 2026",
     "calls": [("coverage_gap", {"request": "Canary Wharf vacancy in Q2 2026"})],
     "selection": {"topics": ["vacancy"], "geographies": ["Canary Wharf"], "periods": [PERIODS[1]], "entity": None}},
]


class ScriptedProvider:
    def __init__(self, turns=MARKET_CHANGE):
        self.turns = copy.deepcopy(turns)
        self.index = -1

    def public_config(self):
        return {"provider": "scripted", "requested_model": None, "live_model": False,
                "label": "OFFLINE FIXED TOOL PLAN; NOT A MODEL EVALUATION"}

    def start_turn(self, context, instructions):
        self.index += 1
        if self.index >= len(self.turns) or context["user_message"] != self.turns[self.index]["prompt"]:
            raise ProviderError("SCRIPTED_INPUT_MISMATCH")
        self.context, self.calls, self.observations = context, list(self.turns[self.index]["calls"]), []
        self.step = 0

    def next_step(self):
        self.step += 1
        if self.calls:
            name, arguments = self.calls.pop(0)
            return Decision(call=ToolCall(f"script_{self.index}_{self.step}", name, json.dumps(arguments)))
        turn = self.turns[self.index]
        if turn.get("use_previous"):
            items = self.context["state"]["previous_answer_items"]
        else:
            items = [{"kind": "comparison" if o["tool"] == "compare_facts" else "coverage", "ref": o["observation_id"]}
                     for o in self.observations if o["result"]["ok"] and o["tool"] in ("compare_facts", "coverage_gap")]
        return Decision(answer={"kind": "answer", "items": items, "clarification": None,
                                "follow_up": None, "selection": turn["selection"]})

    def observe(self, call_id, observation):
        self.observations.append(observation)

    def feedback(self, message):
        raise ProviderError("SCRIPTED_PLAN_REJECTED")
