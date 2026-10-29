#!/usr/bin/env python3
"""Inspect trace structure/support and recorded mode; not a semantic/model grader."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_tools
from agent_loop import Ledger, render_item, CLARIFICATIONS, FOLLOW_UPS, REFUSAL_TEXT
from agent_protocol import strict_json
from contract_validation import validate


def require(condition, message):
    if not condition: raise ValueError(message)


def check_trace(path, require_live=False, expect_provider=None):
    events = [strict_json(line, max_chars=4_000_000) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    require(bool(events) and events[0].get("event") == "run_start", "Missing run header")
    header = events[0]
    require(header.get("trace_version") == "0.1" and header.get("tool_contract_version") == "0.1", "Unsupported trace/contract version")
    require(header["fixture_manifest_sha256"] == hashlib.sha256((ROOT / "inputs/manifest.json").read_bytes()).hexdigest(), "Fixture identity mismatch")
    for relative, expected in header["code_sha256"].items():
        target = (ROOT / relative).resolve()
        require(target.is_relative_to(ROOT), "Invalid source identity path")
        require(hashlib.sha256(target.read_bytes()).hexdigest() == expected, "Code identity mismatch: " + relative)
    live = header["provider"].get("live_model") is True
    provider = header["provider"].get("provider")
    if expect_provider is not None:
        require(provider == expect_provider, "Recorded provider does not match requested review provider")
    if require_live:
        require(live and provider in ("openai", "apple"), "Recorded run is not a supported live mode")
        require(bool(header["source_commit"]) and header["source_dirty"] is False, "Live review requires a recorded clean source checkout")
        require(bool(header["provider"].get("requested_model")), "Requested model missing")
        if provider == "apple":
            config = header["provider"]
            require(config.get("requested_model") == "apple.system.default" and config.get("framework") == "FoundationModels" and config.get("execution") == "on_device", "Invalid Apple model route")
            require(config.get("model_revision_exposed") is False, "Apple model revision must not be invented")
            require("apple_worker.py" in header["code_sha256"] and "apple_provider.py" in header["code_sha256"], "Apple worker source identity missing")
            require(config.get("sdk_package") == "apple-fm-sdk" and config.get("sdk_version") == "0.2.1", "Apple SDK identity missing")
            system = config.get("system_model", {})
            require(system.get("availability") == "available" and bool(system.get("os_version")), "Apple system model unavailable or unidentified")
            require(system.get("sdk_version") == "0.2.1" and bool(system.get("sdk_library_sha256")) and bool(system.get("xcode_version")), "Apple SDK library/build identity missing")
    ledgers, opened, states = {}, {}, {}
    counts = {"turns": 0, "answered": 0, "clarified": 0, "refused": 0, "errors": 0, "tool_calls": 0, "rejections": 0}
    models = set()
    run_id = header["run_id"]
    seen_turns = set()
    for event in events[1:]:
        require(event.get("run_id") == run_id, "Mixed run IDs")
        session = event.get("session_id")
        kind = event["event"]
        if kind == "session_start":
            require(session not in ledgers, "Duplicate session")
            ledgers[session], states[session] = Ledger(), event["state"]
            continue
        require(session in ledgers, "Unknown session")
        turn = event.get("turn_id")
        if kind == "turn_start":
            require(session not in opened and (session, turn) not in seen_turns, "Duplicate or overlapping turn")
            require(event["state_before"] == states[session], "State discontinuity")
            opened[session] = turn
            seen_turns.add((session, turn))
            continue
        require(opened.get(session) == turn, "Event outside its active turn")
        if kind == "provider_step":
            metadata = event["metadata"]
            if live:
                require(bool(metadata.get("response_id")) and bool(metadata.get("returned_model")), "Provider identity missing")
                if provider == "apple":
                    require(metadata.get("returned_model") == "apple.system.default" and metadata.get("framework") == "FoundationModels" and metadata.get("execution") == "on_device" and metadata.get("response_id_origin") == "local_generation_uuid", "Apple generation identity missing")
                models.add(metadata["returned_model"])
        elif kind == "tool_result":
            name, result = event["tool"], event["result"]
            schema = agent_tools.CONTRACT["tools"][name]["response"] if name in agent_tools.CONTRACT["tools"] else agent_tools.CONTRACT["$defs"]["error_envelope"]
            validate(result, schema, agent_tools.CONTRACT)
            if event["executed"]:
                require(agent_tools.invoke(name, event["arguments"]) == result, "Recorded tool result differs from the current preserved fixture")
            else:
                require(result["ok"] is False and result["error"]["code"] == "INVALID_ARGUMENT", "Invalid host reference rejection")
            require(event["observation_id"] not in ledgers[session].observations, "Duplicate observation")
            ledgers[session].ingest(event)
            counts["tool_calls"] += 1
        elif kind == "answer_rejected":
            counts["rejections"] += 1
        elif kind == "turn_end":
            status, claims, text = event["status"], event["claims"], event["final_answer"]
            if status == "answered":
                require(bool(claims), "Answer has no supported items")
                for claim in claims:
                    expected = render_item({"kind": claim["kind"], "ref": claim["ref"]}, ledgers[session])
                    require(claim == expected, "Claim is not the supported observation projection")
                rendered = "\n\n".join("- " + claim["text"] for claim in claims)
                require(text in {rendered, *(rendered + "\n\n" + question for question in FOLLOW_UPS.values())}, "Unreferenced final text")
                counts["answered"] += 1
            elif status == "clarified":
                require(not claims and text in CLARIFICATIONS.values(), "Invalid clarification")
                counts["clarified"] += 1
            elif status == "refused":
                require(not claims and text == REFUSAL_TEXT, "Invalid refusal")
                counts["refused"] += 1
            else:
                require(not claims, "Failed turn published factual claims")
                counts["errors"] += 1
            states[session] = event["state_after"]
            del opened[session]
            counts["turns"] += 1
        else:
            raise ValueError("Unknown trace event: " + kind)
    require(not opened and counts["turns"] > 0, "Incomplete or empty conversation")
    if require_live:
        require(bool(models) and counts["answered"] > 0 and counts["tool_calls"] > 0, "No recorded live tool/answer interaction")
        require(counts["errors"] == 0, "Unresolved live-turn errors; preserve trace and resolve before acceptance")
    return {"mode": "live_recorded" if live else "offline", "provider": provider, "source_commit": header["source_commit"],
            "source_dirty": header["source_dirty"], "requested_model": header["provider"].get("requested_model"),
            "returned_models": sorted(models), "sessions": len(ledgers), **counts,
            "semantic_acceptance": "PENDING_USER; CP5 case evaluation remains separate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--expect-provider", choices=["apple", "openai"], help="Require the provider selected for this review")
    options = parser.parse_args()
    try:
        print(json.dumps(check_trace(options.trace, options.require_live, options.expect_provider), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("FAIL | CP4 trace | " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
