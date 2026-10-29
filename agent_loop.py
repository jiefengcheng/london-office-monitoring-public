"""Bounded evidence agent, small state, reference-gated answers and JSONL tracing."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

import agent_tools
from agent_protocol import ROOT, Decision, Provider, ProviderError, ToolCall, check_answer_shape, strict_json
from contract_validation import ContractViolation

CLARIFICATIONS = {"geography": "Which publisher geography should I use?",
                  "period": "Which observation periods should I compare?",
                  "metric": "Which metric do you want to inspect?",
                  "entity": "Which building or entity do you mean?",
                  "referent": "Which geography or asset do you mean?",
                  "scope": "Please specify the metric, geography and observation period."}
FOLLOW_UPS = {"narrow_geography": "Would you like to narrow the publisher geography?",
              "compare_periods": "Would you like to compare compatible observation periods?",
              "inspect_source": "Would you like to inspect the original source?",
              "project_history": "Would you like to inspect a building's dated project history?",
              "coverage": "Would you like to check a specific evidence gap?"}
REFUSAL_TEXT = "I can help with the curated London office evidence. Please specify a supported metric, geography, period or asset."


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_identity():
    manifest = json.loads((ROOT / "inputs/manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["sha256"].items():
        if _hash(ROOT / relative) != expected:
            raise ValueError(f"Original input hash mismatch: {relative}")
    try:
        commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=normal"], text=True))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    code = ["agent.py", "agent_loop.py", "agent_protocol.py", "providers.py", "apple_provider.py", "agent_tools.py",
            "contract_validation.py", "contracts/tool-contract.json", "contracts/cross-section-groups.json",
            "contracts/agent-answer.schema.json", "agent_skills/CORE.md", "agent_skills/APPLE.md",
            "apple_worker.py", "requirements.lock", "requirements-apple.lock", "requirements-apple-build.lock", "scripts/install.py", "scripts/check_cp4_trace.py",
            "scripts/check_cp4_live_review.py"]
    code += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "agent_skills").glob("*/SKILL.md"))]
    return {"source_commit": commit, "source_dirty": dirty,
            "fixture_manifest_sha256": _hash(ROOT / "inputs/manifest.json"),
            "evidence_database_sha256": _hash(ROOT / "evidence/london_office_evidence_pack/evidence.sqlite"),
            "code_sha256": {name: _hash(ROOT / name) for name in code}}


class TraceWriter:
    def __init__(self, path, provider_config):
        self.path = Path(path).resolve()
        if self.path.suffix != ".jsonl":
            raise ValueError("Trace path must end in .jsonl")
        if self.path.is_relative_to(ROOT) and not self.path.is_relative_to(ROOT / "output"):
            raise ValueError("In-repository traces must be stored under output/")
        identity = run_identity()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", encoding="utf-8")
        self.run_id = uuid.uuid4().hex
        self.emit("run_start", trace_version="0.1", agent_protocol_version="0.1", host="python",
                  tool_contract_version=agent_tools.VERSION, provider=provider_config, **identity)

    def emit(self, event, **values):
        self._file.write(json.dumps({"event": event, "run_id": self.run_id,
                                    "at": datetime.now(timezone.utc).isoformat(), **values},
                                   ensure_ascii=False, allow_nan=False) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()


@dataclass
class State:
    topics: list = field(default_factory=list)
    geographies: list = field(default_factory=list)
    periods: list = field(default_factory=list)
    entity: str | None = None
    evidence_ids: list = field(default_factory=list)
    previous_answer_items: list = field(default_factory=list)


class Ledger:
    def __init__(self):
        self.facts, self.sources, self.observations = {}, {}, {}
        self.known_fact_ids = set()
        self.known_source_ids = set()

    def ingest(self, observation):
        self.observations[observation["observation_id"]] = copy.deepcopy(observation)
        result = observation["result"]
        if not result["ok"]:
            return
        name, data = observation["tool"], result["data"]
        facts = []
        if name == "get_fact": facts = [data]
        elif name == "search_facts": facts = data["facts"]
        elif name == "compare_facts":
            facts = [data["earlier"], data["later"]] if data["mode"] == "time_series" else [data["a"], data["b"]]
        elif name == "get_source":
            self.sources[data["source_id"]] = data
            self.known_source_ids.add(data["source_id"])
            self.known_fact_ids.update(data["fact_ids"])
        elif name == "coverage_gap":
            self.known_fact_ids.update(data["available_fact_ids"])
            for row in data["coverage_rows"]:
                self.known_source_ids.update(row["sources"])
        for fact in facts:
            self.facts[fact["fact_id"]] = fact
            self.known_fact_ids.add(fact["fact_id"])
            self.sources[fact["source_id"]] = fact["source"]
            self.known_source_ids.add(fact["source_id"])

    def context(self):
        fact_keys = ("fact_id", "topic", "metric", "entity", "geography", "segment", "value_num", "value_text",
                     "unit", "period_kind", "period_start", "period_end", "status", "source_id", "caveat",
                     "series_key", "geography_basis")
        observations = []
        for observation in self.observations.values():
            if not observation["result"]["ok"]: continue
            data, name = observation["result"]["data"], observation["tool"]
            if name == "compare_facts":
                keys = ("mode", "difference", "difference_unit", "basis_points", "relative_change_percent")
                item = {key: data[key] for key in keys}
                item["fact_ids"] = [data[key]["fact_id"] for key in (("earlier", "later") if data["mode"] == "time_series" else ("a", "b"))]
            elif name == "coverage_gap":
                item = {key: data[key] for key in ("assessment", "requested_scope", "unresolved_fields", "available_fact_ids")}
            else: continue
            observations.append({"observation_id": observation["observation_id"], "tool": name, "data": item})
        return {"facts": [{key: fact[key] for key in fact_keys} for fact in self.facts.values()],
                "sources": [{key: source[key] for key in ("source_id", "title", "access_status", "notes", "published_at")} for source in self.sources.values()],
                "fact_ids": sorted(self.known_fact_ids),
                "source_ids": sorted(self.known_source_ids), "observations": observations,
                "searches": [{"observation_id":o["observation_id"], "arguments":o["arguments"],
                              "count":o["result"]["data"]["count"], "truncated":o["result"]["data"]["truncated"]}
                             for o in self.observations.values() if o["tool"]=="search_facts" and o["result"]["ok"] and "arguments" in o]}


def _citation(fact):
    return (f"[{fact['fact_id']}/{fact['source_id']}]({fact['source_url']}) — {fact['source_publisher']}; "
            f"published {fact['source_published_at']}; retrieved {fact['source_retrieved_at']}; {fact['locator']}")


def _fact_scope(fact):
    return {key: fact[key] for key in ("fact_id", "geography", "geography_basis", "segment", "metric",
                                      "period_kind", "period_start", "period_end", "status", "source_id", "locator")}


def _unit(value):
    return {"GBP_per_sq_ft": "GBP/sq ft", "million_sq_ft": "million sq ft", "sq_ft": "sq ft",
            "sq_m": "sq m", "percent_of_ERV": "percent of ERV"}.get(value, value.replace("_", " "))


def render_item(item, ledger):
    kind, reference = item["kind"], item["ref"]
    facts = []
    if kind == "fact":
        fact = ledger.facts.get(reference)
        if fact is None: raise ValueError("Unobserved fact reference")
        value = fact["value_text"] if fact["value_text"] is not None else str(fact["value_num"])
        text = (f"{fact['geography']} — {fact['metric'].replace('_', ' ')}: {value} {_unit(fact['unit'])}; "
                f"{fact['segment']}; {fact['entity']}; {fact['period_kind'].replace('_', ' ')} "
                f"{fact['period_start']} to {fact['period_end']}; {fact['status'].replace('_', ' ')}.")
        if fact.get("paraphrase"):
            text += " Source observation: " + fact["paraphrase"]
        facts = [fact]
    elif kind == "source":
        source = ledger.sources.get(reference)
        if source is None: raise ValueError("Unobserved source reference")
        text = (f"[{reference}: {source['title']}]({source['url']}) — {source['publisher']}; "
                f"published {source['published_at'] or 'date not established'}; retrieved {source['retrieved_at']}; "
                f"{source['access_status']}; role: {source['role'].replace('_', ' ')}. {source['notes']}")
    else:
        observation = ledger.observations.get(reference)
        required_tool = "compare_facts" if kind == "comparison" else "coverage_gap"
        if observation is None or observation["tool"] != required_tool or not observation["result"]["ok"]:
            raise ValueError("Unobserved or unsuccessful result reference")
        data = observation["result"]["data"]
        if kind == "comparison":
            if data["mode"] == "time_series":
                a, b = data["earlier"], data["later"]
                text = (f"{a['geography']} — {a['metric'].replace('_', ' ')}: {a['value_num']} {_unit(a['unit'])} "
                        f"({a['period_start']} to {a['period_end']}) → {b['value_num']} {_unit(b['unit'])} "
                        f"({b['period_start']} to {b['period_end']}); change {data['difference']} {_unit(data['difference_unit'])}.")
            else:
                a, b = data["a"], data["b"]
                text = (f"{b['geography']} minus {a['geography']} — {a['metric'].replace('_', ' ')}: "
                        f"{data['difference']} {_unit(data['difference_unit'])}; same period {a['period_start']} to {a['period_end']}; "
                        f"{a['segment']}. Observations: {a['value_num']} and {b['value_num']} {_unit(a['unit'])}.")
            if data["basis_points"] is not None: text += f" Equivalent: {data['basis_points']} basis points."
            if data["relative_change_percent"] is not None: text += f" Approximate relative change: {data['relative_change_percent']}%."
            text += " " + data["rounding_caveat"]
            facts = [a, b]
        else:
            scope = data["requested_scope"]
            labels = [str(scope[key]).replace("_", " ") for key in
                      ("geography", "metric", "entity", "period_start", "period_end") if key in scope]
            text = "Fixture coverage — " + "; ".join(labels) + ": " + data["message"]
            if data["unresolved_fields"]: text += " Clarify: " + ", ".join(key.replace("_", " ") for key in data["unresolved_fields"]) + "."
            text += " " + " ".join(row["notes"] for row in data["coverage_rows"])
    for fact in facts:
        text += "\n  " + _citation(fact)
    caveats = list(dict.fromkeys(fact["caveat"] for fact in facts if fact["caveat"]))
    if caveats: text += "\n  " + " ".join(caveats)
    return {"kind": kind, "ref": reference, "text": text,
            "fact_ids": [fact["fact_id"] for fact in facts],
            "source_ids": list(dict.fromkeys([fact["source_id"] for fact in facts] + ([reference] if kind == "source" else []))),
            "scopes": [_fact_scope(fact) for fact in facts]}


class EvidenceAgent:
    def __init__(self, provider: Provider, trace=None, max_steps=12, on_tool=None):
        if type(max_steps) is not int or not 1 <= max_steps <= 20:
            raise ValueError("max_steps must be an integer from 1 to 20")
        self.provider, self.trace, self.max_steps = provider, trace, max_steps
        self.on_tool = on_tool
        self.instructions = (ROOT / "agent_skills/CORE.md").read_text(encoding="utf-8")
        for name in ("market_briefing", "project_history", "coverage_check"):
            self.instructions += "\n" + (ROOT / "agent_skills" / name / "SKILL.md").read_text(encoding="utf-8")
        self.new_session()

    def emit(self, event, **values):
        if self.trace: self.trace.emit(event, session_id=self.session_id, **values)

    def new_session(self):
        self.state, self.ledger = State(), Ledger()
        self.session_id, self.turn = uuid.uuid4().hex, 0
        self.emit("session_start", state=asdict(self.state))

    def _execute(self, call, user_text, observation_id):
        executed = False
        arguments = None
        try:
            arguments = strict_json(call.arguments)
            if not isinstance(arguments, dict): raise ValueError("Arguments must be an object")
            references = [arguments[key] for key in ("fact_id", "fact_id_a", "fact_id_b") if key in arguments]
            if any(not isinstance(ref, str) or (ref not in self.ledger.known_fact_ids and not re.search(r"(?<!\w)" + re.escape(ref) + r"(?!\w)", user_text)) for ref in references):
                raise ValueError("Fact references must come from observed evidence or the user's explicit IDs")
            source = arguments.get("source_id")
            if source is not None and (not isinstance(source, str) or (source not in self.ledger.known_source_ids and not re.search(r"(?<!\w)" + re.escape(source) + r"(?!\w)", user_text))):
                raise ValueError("Source references must come from observed evidence or the user's explicit IDs")
            if call.name == "search_facts":
                signature = tuple(sorted((key, str(arguments[key]).casefold()) for key in arguments if key != "limit"))
                for observation in self.ledger.observations.values():
                    prior = observation.get("arguments") or {}
                    if (observation["tool"] == "search_facts" and observation["result"]["ok"]
                            and tuple(sorted((key, str(prior[key]).casefold()) for key in prior if key != "limit")) == signature
                            and not (observation["result"]["data"]["truncated"]
                                     and type(arguments.get("limit", 20)) is int
                                     and arguments.get("limit", 20) > observation["result"]["data"]["limit"])):
                        raise ValueError("Do not repeat a successful identical search; use a new place, metric or compare_facts")
            executed = True
            result = agent_tools.invoke(call.name, arguments)
        except ValueError as error:
            result = {"contract_version": agent_tools.VERSION, "ok": False, "data": None,
                      "error": {"code": "INVALID_ARGUMENT", "message": str(error)}}
        observation = {"observation_id": observation_id, "tool": call.name, "arguments": arguments,
                       "executed": executed, "result": result}
        if arguments is None and isinstance(call.arguments, str):
            observation["raw_arguments_sha256"] = hashlib.sha256(call.arguments.encode("utf-8")).hexdigest()
            observation["raw_arguments_length"] = len(call.arguments)
            if len(call.arguments) <= 100_000:
                observation["rejected_raw_arguments"] = call.arguments
        self.ledger.ingest(observation)
        return observation

    def _finish(self, answer):
        check_answer_shape(answer)
        if len({(item["kind"], item["ref"]) for item in answer["items"]}) != len(answer["items"]):
            raise ValueError("Duplicate answer item")
        if answer["kind"] == "answer":
            if not answer["items"] or answer["clarification"] is not None:
                raise ValueError("An answer needs supported items and no clarification code")
            claims = [render_item(item, self.ledger) for item in answer["items"]]
            text = "\n\n".join("- " + claim["text"] for claim in claims)
        else:
            if answer["items"]: raise ValueError("Clarification/refusal cannot publish factual items")
            if any(value is not None for value in answer["selection"].values()):
                raise ValueError("Clarification/refusal must retain the previous selection")
            if answer["follow_up"] is not None:
                raise ValueError("Clarification/refusal must use only its primary question")
            claims = []
            if answer["kind"] == "clarify":
                if answer["clarification"] is None: raise ValueError("Clarification code required")
                text = CLARIFICATIONS[answer["clarification"]]
            else:
                if answer["clarification"] is not None: raise ValueError("Refusal must not include a clarification code")
                text = REFUSAL_TEXT
        candidate = copy.deepcopy(self.state)
        for key, value in answer["selection"].items():
            if value is not None:
                if key == "periods":
                    for period in value:
                        for bound in ("start", "end"):
                            if date.fromisoformat(period[bound]).isoformat() != period[bound]: raise ValueError("Invalid selected period")
                        if period["start"] > period["end"]: raise ValueError("Reversed selected period")
                setattr(candidate, key, value if key != "entity" or value else None)
        if answer["kind"] == "answer":
            candidate.evidence_ids = list(dict.fromkeys(f for claim in claims for f in claim["fact_ids"]))
            candidate.previous_answer_items = copy.deepcopy(answer["items"])
        if answer["follow_up"] is not None: text += "\n\n" + FOLLOW_UPS[answer["follow_up"]]
        self.state = candidate
        return text, claims

    def ask(self, user_text):
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 4000:
            raise ValueError("Message must contain 1–4000 characters")
        if len(self.ledger.observations) >= 200:
            raise ValueError("Conversation observation limit reached; use /new")
        self.turn += 1
        turn_id = f"T{self.turn}"
        before = asdict(self.state)
        started = time.monotonic()
        self.emit("turn_start", turn_id=turn_id, user_message=user_text, state_before=before)
        status, text, claims, error_code = "budget_exhausted", "The tool-step budget was reached. Narrow the request or start a new conversation.", [], None
        diagnostic_fields = {}
        try:
            self.provider.start_turn({"user_message": user_text, "state": before,
                                      "known_evidence": self.ledger.context()}, self.instructions)
            call_ids = set()
            for step in range(1, self.max_steps + 1):
                decision = self.provider.next_step()
                if not isinstance(decision, Decision): raise ProviderError("INVALID_DECISION")
                try:
                    json.dumps({"answer": decision.answer, "metadata": decision.metadata}, allow_nan=False)
                except (ValueError, TypeError, RecursionError):
                    raise ProviderError("INVALID_DECISION_JSON") from None
                self.emit("provider_step", turn_id=turn_id, step=step, metadata=decision.metadata)
                if (decision.call is None) == (decision.answer is None):
                    raise ProviderError("AMBIGUOUS_DECISION")
                if decision.call is not None:
                    if len(self.ledger.observations) >= 200:
                        raise ProviderError("OBSERVATION_LIMIT")
                    call = decision.call
                    if not isinstance(call, ToolCall) or not isinstance(call.name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", call.name):
                        raise ProviderError("INVALID_TOOL_CALL")
                    if not isinstance(call.call_id, str) or not call.call_id or call.call_id in call_ids:
                        raise ProviderError("DUPLICATE_OR_INVALID_CALL_ID")
                    call_ids.add(call.call_id)
                    observation = self._execute(call, user_text, f"{turn_id}.O{len(call_ids)}")
                    self.emit("tool_result", turn_id=turn_id, step=step, call_id=call.call_id, **observation)
                    if self.on_tool: self.on_tool(observation)
                    self.provider.observe(call.call_id, observation)
                else:
                    try:
                        text, claims = self._finish(decision.answer)
                    except (ValueError, ContractViolation, KeyError, TypeError) as error:
                        self.emit("answer_rejected", turn_id=turn_id, step=step, reason=str(error), candidate=decision.answer)
                        self.provider.feedback("ANSWER_REJECTED: " + str(error) + ". Use observed references and the answer-selection schema only.")
                        continue
                    status = {"answer": "answered", "clarify": "clarified", "refuse": "refused"}[decision.answer["kind"]]
                    break
        except ProviderError as error:
            status, error_code = "provider_error", error.code
            if error.diagnostic: diagnostic_fields["provider_diagnostic"] = error.diagnostic
            text = f"The model provider could not complete this turn ({error.code}). Check the local provider configuration and retry."
        self.emit("turn_end", turn_id=turn_id, status=status, error_code=error_code, final_answer=text,
                  claims=claims, state_after=asdict(self.state), elapsed_ms=round((time.monotonic() - started) * 1000), **diagnostic_fields)
        return {"turn_id": turn_id, "status": status, "answer": text, "claims": claims,
                "state_before": before, "state_after": asdict(self.state), "error_code": error_code, **diagnostic_fields}
