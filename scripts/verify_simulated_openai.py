#!/usr/bin/env python3
"""Exercise the OpenAI Responses adapter against a local deterministic HTTP simulator."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIMULATOR_KEY = "codex-local-simulation-only"
SIM_MODEL = "codex-simulated-responses-v1"
PROMPT = "Compare Central London vacancy in Q1 and Q2 2026."


def response(response_id, output):
    return {"id": response_id, "model": SIM_MODEL, "status": "completed", "output": output,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}


def output_text(answer):
    return [{"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": json.dumps(answer, separators=(",", ":"))}]}]


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def reply(self, status, value):
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/v1/responses":
                self.reply(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                inputs = payload.get("input", [])
                observations = []
                for item in inputs:
                    if isinstance(item, dict) and item.get("type") == "function_call_output":
                        observations.append(json.loads(item["output"]))
                state["requests"].append({
                    "path": self.path,
                    "model": payload.get("model"),
                    "authorization_matches_local_dummy": self.headers.get("Authorization") == "Bearer " + SIMULATOR_KEY,
                    "store": payload.get("store"),
                    "parallel_tool_calls": payload.get("parallel_tool_calls"),
                    "tool_names": [item.get("name") for item in payload.get("tools", []) if isinstance(item, dict)],
                    "strict_json_schema": payload.get("text", {}).get("format", {}).get("strict") is True,
                    "function_results_seen": len(observations),
                })
                if not observations:
                    output = [{"type": "function_call", "call_id": "sim_search_1", "name": "search_facts",
                               "arguments": json.dumps({"topic": "vacancy", "geography": "Central London",
                                                         "period_start": "2026-01-01", "period_end": "2026-06-30",
                                                         "limit": 20})}]
                    self.reply(200, response("resp_sim_1", output))
                    return
                last = observations[-1]
                if last.get("tool") == "search_facts" and last.get("result", {}).get("ok") is True:
                    facts = last["result"]["data"].get("facts", [])
                    ids = {fact.get("fact_id") for fact in facts}
                    if not {"F01", "F02"}.issubset(ids):
                        raise ValueError("the fixture search did not return F01 and F02")
                    output = [{"type": "function_call", "call_id": "sim_compare_1", "name": "compare_facts",
                               "arguments": json.dumps({"fact_id_a": "F01", "fact_id_b": "F02"})}]
                    self.reply(200, response("resp_sim_2", output))
                    return
                if last.get("tool") == "compare_facts" and last.get("result", {}).get("ok") is True:
                    answer = {"kind": "answer", "items": [{"kind": "comparison", "ref": last["observation_id"]}],
                              "clarification": None, "follow_up": None,
                              "selection": {"topics": ["vacancy"], "geographies": ["Central London"],
                                            "periods": [{"start": "2026-01-01", "end": "2026-03-31"},
                                                        {"start": "2026-04-01", "end": "2026-06-30"}],
                                            "entity": None}}
                    self.reply(200, response("resp_sim_3", output_text(answer)))
                    return
                raise ValueError("unexpected request sequence")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                state["errors"].append(type(error).__name__ + ": " + str(error))
                self.reply(400, {"error": "simulator rejected the request"})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New output directory under output/; previous runs are never overwritten")
    args = parser.parse_args(argv)
    directory = args.output_dir.resolve()
    if not directory.is_relative_to((ROOT / "output").resolve()) or directory.exists():
        parser.error("Choose a new, not-yet-created directory under this checkout's output/")
    directory.mkdir(parents=True)
    trace = directory / "simulated-openai.jsonl"
    state = {"requests": [], "errors": []}
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/responses"
    environment = {**os.environ, "MODEL_PROVIDER": "openai", "MODEL_NAME": SIM_MODEL,
                   "MODEL_API_KEY": SIMULATOR_KEY, "OPENAI_API_KEY": "",
                   "MODEL_API_ENDPOINT": endpoint}
    command = [sys.executable, str(ROOT / "agent.py"), "--provider", "openai", "--message", PROMPT,
               "--trace", str(trace)]
    try:
        run = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
    (directory / "agent-console.txt").write_text(run.stdout + run.stderr, encoding="utf-8")
    check_command = [sys.executable, str(ROOT / "scripts/check_cp4_trace.py"), str(trace), "--expect-provider", "openai"]
    check = subprocess.run(check_command, cwd=ROOT, text=True, capture_output=True)
    (directory / "trace-check.txt").write_text(check.stdout + check.stderr, encoding="utf-8")
    live_check_command = [sys.executable, str(ROOT / "scripts/check_cp4_trace.py"), str(trace),
                          "--require-live", "--expect-provider", "openai"]
    live_check = subprocess.run(live_check_command, cwd=ROOT, text=True, capture_output=True)
    (directory / "live-boundary-check.txt").write_text(live_check.stdout + live_check.stderr, encoding="utf-8")
    try:
        trace_events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        report = json.loads(check.stdout) if check.returncode == 0 else {}
        header = trace_events[0]
        passed = (run.returncode == 0 and check.returncode == 0 and not state["errors"]
                  and len(state["requests"]) == 3 and all(item["authorization_matches_local_dummy"] for item in state["requests"])
                  and all(item["path"] == "/v1/responses" and item["model"] == SIM_MODEL
                          and item["store"] is False and item["parallel_tool_calls"] is False
                          and item["strict_json_schema"] for item in state["requests"])
                  and header["provider"].get("live_model") is False
                  and header["provider"].get("simulated_endpoint") is True
                  and report.get("mode") == "offline" and report.get("tool_calls") == 2
                  and live_check.returncode != 0 and "not a supported live mode" in live_check.stderr
                  and report.get("answered") == 1 and report.get("errors") == 0
                  and "-0.2 percentage points" in run.stdout and "-20 basis points" in run.stdout)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        passed = False
    (directory / "simulator-report.json").write_text(json.dumps({
        "profile": "codex-local-simulated-openai-v1",
        "endpoint": endpoint,
        "requested_model": SIM_MODEL,
        "http_exchanges": len(state["requests"]),
        "tool_calls": 2 if len(state["requests"]) == 3 else None,
        "provider_errors": state["errors"],
        "agent_exit": run.returncode,
        "trace_checker_exit": check.returncode,
        "require_live_boundary_exit": live_check.returncode,
        "trace_mode": "offline simulation, never live OpenAI",
        "result": "PASS_LOCAL_PIPELINE_ONLY" if passed else "FAIL_LOCAL_PIPELINE",
        "scope": "Real agent and provider HTTP client against a deterministic loopback Responses API simulator; no external API request, model inference, credentials, account access or quota check"
    }, indent=2) + "\n", encoding="utf-8")
    print(run.stdout, end="")
    if run.stderr:
        print(run.stderr, end="", file=sys.stderr)
    print(f"SIMULATOR | loopback HTTP exchanges={len(state['requests'])}; no OpenAI API request made")
    print("PASS | simulator traces are explicitly rejected by the live-acceptance checker")
    print(f"{ 'PASS_LOCAL_PIPELINE_ONLY' if passed else 'FAIL_LOCAL_PIPELINE' } | simulated OpenAI Responses pipeline | {directory / 'simulator-report.json'}")
    print("LIMIT | validates local HTTP, provider parsing, agent tool loop, evidence rendering and trace checks; does not verify the OpenAI-hosted endpoint, model access, credentials, quota or live model behavior")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print("FAIL | simulated OpenAI verification | " + str(error), file=sys.stderr)
        raise SystemExit(1)
