#!/usr/bin/env python3
"""Interactive London evidence agent; scripted runs are explicitly offline."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from agent_loop import EvidenceAgent, TraceWriter
from agent_protocol import ROOT, ProviderError
from providers import OpenAIResponsesProvider
from apple_provider import AppleFoundationModelsProvider, MODEL as APPLE_MODEL
from question_guidance import make_recovery, with_recovery

ENV_KEYS = {"MODEL_PROVIDER", "MODEL_NAME", "MODEL_API_KEY", "OPENAI_API_KEY",
            "MODEL_API_ENDPOINT",
            "MODEL_TIMEOUT_SECONDS", "MODEL_MAX_OUTPUT_TOKENS",
            "APPLE_TIMEOUT_SECONDS", "APPLE_MAX_OUTPUT_TOKENS"}


def load_environment(path=ROOT / ".env"):
    """Read only simple KEY=VALUE assignments; never source a shell file."""
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key not in ENV_KEYS or key in values:
                raise ValueError(".env must contain unique supported KEY=VALUE assignments")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]: raise ValueError("Unclosed .env quote")
                value = value[1:-1]
            values[key] = value
    return {**values, **{key: os.environ[key] for key in ENV_KEYS if key in os.environ}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["apple", "openai", "scripted"])
    parser.add_argument("--model", help="Exact model identifier; overrides MODEL_NAME")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--interactive", action="store_true")
    mode.add_argument("--message")
    mode.add_argument("--case", choices=["market_change"], help="Offline scripted case only")
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--max-steps", type=int, default=12)
    options = parser.parse_args(argv)
    trace = None
    try:
        environment = load_environment()
        name = options.provider or environment.get("MODEL_PROVIDER") or "openai"
        if name == "scripted":
            if options.case is None: raise ValueError("Scripted mode requires --case; it is not an interactive model")
            from eval.scripted_scenarios import MARKET_CHANGE, ScriptedProvider
            provider = ScriptedProvider()
        elif name == "apple":
            if options.case: raise ValueError("--case is reserved for offline scripted verification")
            if options.model and options.model != APPLE_MODEL:
                raise ValueError("Apple uses apple.system.default; --model cannot select Apple model weights")
            provider = AppleFoundationModelsProvider(
                timeout=int(environment.get("APPLE_TIMEOUT_SECONDS") or "90"),
                max_output_tokens=int(environment.get("APPLE_MAX_OUTPUT_TOKENS") or "1024"))
        elif name == "openai":
            if options.case: raise ValueError("--case is reserved for offline scripted verification")
            provider = OpenAIResponsesProvider(options.model or environment.get("MODEL_NAME", ""),
                environment.get("MODEL_API_KEY") or environment.get("OPENAI_API_KEY", ""),
                timeout=int(environment.get("MODEL_TIMEOUT_SECONDS") or "45"),
                max_output_tokens=int(environment.get("MODEL_MAX_OUTPUT_TOKENS") or "4096"),
                endpoint=environment.get("MODEL_API_ENDPOINT") or None)
        else:
            raise ValueError("Unsupported MODEL_PROVIDER; use apple, openai or explicit scripted mode")
        path = options.trace or ROOT / "output" / ("cp4-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".jsonl")
        trace = TraceWriter(path, provider.public_config())
        def show_tool(observation):
            result = observation["result"]
            print(f"Evidence tool: {observation['tool']} — " + ("ok" if result["ok"] else result["error"]["code"]))

        agent = EvidenceAgent(provider, trace, options.max_steps, on_tool=show_tool)
        print("OFFLINE SCRIPTED VERIFICATION — NOT A MODEL EVALUATION" if name == "scripted"
              else f"London office evidence agent — {'Apple Intelligence / Foundation Models' if name == 'apple' else 'OpenAI Responses'}; requested model: {provider.model}")
        print(f"Trace: {trace.path}")
        print("Evidence: curated historical fixture. Source dates and caveats remain visible.")
        if name == "scripted":
            results = []
            for turn in MARKET_CHANGE:
                print("\nYou:", turn["prompt"])
                result = agent.ask(turn["prompt"])
                print(result["answer"])
                results.append(result)
            return 0 if all(r["status"] == "answered" for r in results) else 1
        if options.message is not None:
            result = agent.ask(options.message)
            print(result["answer"])
            return 0 if result["status"] in ("answered", "clarified", "refused") else 1
        print("Commands: /new resets the conversation; /state shows the selected scope; /quit exits.")
        code = 0
        while True:
            try:
                message = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return code
            if not message: continue
            if message == "/quit": return code
            if message == "/new":
                agent.new_session()
                print("New conversation; selections and previous evidence references cleared.")
                continue
            if message == "/state":
                print(json.dumps(asdict(agent.state), ensure_ascii=False, indent=2))
                continue
            try:
                result = agent.ask(message)
            except ValueError as error:
                print(str(error))
                continue
            print(result["answer"])
            if result["status"] not in ("answered", "clarified", "refused"):
                code = 1  # A later answer or /new must not erase a failed turn.
    except ProviderError as error:
        base=f"Configuration/provider error: {error.code}. Apple: install the pinned official apple-fm-sdk in your active Python environment and enable Apple Intelligence on a supported Mac; see README. OpenAI: explicitly select --provider openai and set MODEL_NAME and MODEL_API_KEY (or OPENAI_API_KEY) locally. No automatic fallback is used."
        print(with_recovery(base,make_recovery(options.message or "",{},"configuration_error",[],{},error_code=error.code)),file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        base=f"Startup/input error: {error}"
        print(base if "Suggested supported question:" in base else with_recovery(base,make_recovery(options.message or "",{},"configuration_error",[],{})),file=sys.stderr)
        return 2
    finally:
        if trace: trace.close()


if __name__ == "__main__":
    sys.exit(main())
