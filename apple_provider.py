"""Keyless on-device Foundation Models decisions; Python owns tools and evidence."""
from __future__ import annotations

import copy
import json
import os
import platform
import subprocess
import sys
import uuid

from agent_loop import Ledger
from agent_protocol import ROOT, Decision, ProviderError, ToolCall, strict_json

MODEL = "apple.system.default"  # Logical system model; Apple exposes no weight revision here.
SOURCE = ROOT / "apple_worker.py"
ERROR_CODES = {"APPLE_DEVICE_NOT_ELIGIBLE", "APPLE_INTELLIGENCE_NOT_ENABLED", "APPLE_MODEL_NOT_READY",
               "APPLE_MODEL_UNAVAILABLE", "APPLE_INPUT_LIMIT", "APPLE_INVALID_OUTPUT_LIMIT",
               "APPLE_CONTEXT_LIMIT", "APPLE_GENERATION_FAILED", "APPLE_RATE_LIMITED",
               "APPLE_GUARDRAIL_VIOLATION", "APPLE_REFUSAL", "APPLE_UNSUPPORTED_GUIDE",
               "APPLE_UNSUPPORTED_LOCALE", "APPLE_MODEL_TIMEOUT", "APPLE_SDK_NOT_INSTALLED",
               "APPLE_SDK_VERSION_MISMATCH", "APPLE_SDK_LIBRARY_MISSING", "APPLE_INVALID_DECISION"}
ERROR_CODES.update("APPLE_INVALID_DECISION_"+stage for stage in ("CALENDAR","FINAL_SELECTION","REFERENCES","PLAN","ACTION","COMPARISON","REPEATED_SEARCH"))
TOOL_FIELDS = {
    "search_facts": {"topic", "geography", "metric", "entity", "period_start", "period_end", "limit"},
    "get_fact": {"fact_id"}, "get_source": {"source_id"},
    "compare_facts": {"fact_id_a", "fact_id_b", "mode"},
    "coverage_gap": {"request", "topic", "geography", "metric", "entity", "period_start", "period_end"},
}


class AppleFoundationModelsProvider:
    def __init__(self, timeout=90, max_output_tokens=1024):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ProviderError("APPLE_REQUIRES_APPLE_SILICON_MAC")
        if tuple(map(int, platform.mac_ver()[0].split(".")[:2])) < (26, 4):
            raise ProviderError("APPLE_REQUIRES_MACOS_26_4")
        if type(timeout) is not int or not 1 <= timeout <= 180:
            raise ProviderError("APPLE_INVALID_TIMEOUT")
        if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 2048:
            raise ProviderError("APPLE_INVALID_OUTPUT_LIMIT")
        self.model, self.timeout, self.max_output_tokens = MODEL, timeout, max_output_tokens
        probe = self._invoke(probe=True)
        self.identity = probe["metadata"]
        self._context, self._pending, self._history, self._feedback = {}, None, [], []
        self._plan = None
        self._ledger = Ledger()

    def _invoke(self, payload=None, probe=False):
        # Do not forward API credentials from the user's environment to the helper.
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME") if key in os.environ}
        try:
            result = subprocess.run([sys.executable, str(SOURCE), *(["--probe"] if probe else [])],
                input=None if probe else json.dumps(payload, ensure_ascii=False, allow_nan=False),
                capture_output=True, text=True, timeout=self.timeout, env=environment)
            if result.returncode != 0: raise ProviderError("APPLE_HELPER_FAILED")
            response = strict_json(result.stdout, max_chars=200_000)
            if not isinstance(response, dict): raise ValueError("Invalid helper envelope")
            if response.get("ok") is not True:
                code = response.get("error")
                raise ProviderError(code if code in ERROR_CODES else "APPLE_HELPER_FAILED", diagnostic=response.get("diagnostic"))
            identity = response.get("metadata")
            if not isinstance(identity, dict) or identity.get("provider") != "apple" or identity.get("returned_model") != MODEL or identity.get("framework") != "FoundationModels" or identity.get("execution") != "on_device" or identity.get("availability") != "available":
                raise ProviderError("APPLE_IDENTITY_MISSING")
            if identity.get("sdk_package") != "apple-fm-sdk" or identity.get("sdk_version") != "0.2.1":
                raise ProviderError("APPLE_IDENTITY_MISSING")
            if not probe and (not isinstance(identity.get("response_id"), str) or not identity["response_id"].startswith("apple-local-")):
                raise ProviderError("APPLE_IDENTITY_MISSING")
            return response
        except subprocess.TimeoutExpired:
            raise ProviderError("APPLE_TIMEOUT") from None
        except (OSError, ValueError, TypeError):
            raise ProviderError("APPLE_INVALID_HELPER_RESPONSE") from None

    def public_config(self):
        return {"provider": "apple", "requested_model": MODEL, "live_model": True,
                "framework": "FoundationModels", "execution": "on_device", "api_key_required": False,
                "timeout_seconds": self.timeout, "max_output_tokens": self.max_output_tokens,
                "model_revision_exposed": False, "sdk_package": "apple-fm-sdk", "sdk_version": "0.2.1",
                "system_model": copy.deepcopy(self.identity)}

    def start_turn(self, context, instructions):
        self._context = copy.deepcopy(context)
        # Dedicated compact prompt for the small on-device model. The shared
        # Python gateway, state and evidence validators remain authoritative.
        self._instructions = (ROOT / "agent_skills/APPLE.md").read_text()
        self._pending, self._history, self._feedback, self._ledger = None, [], [], Ledger()
        self._plan = None

    def _known(self):
        before = self._context["known_evidence"]
        after = self._ledger.context()
        result = {}
        for key, identifier in (("facts", "fact_id"), ("sources", "source_id"), ("observations", "observation_id"), ("searches", "observation_id")):
            result[key] = list({item[identifier]: item for item in before.get(key, []) + after.get(key, [])}.values())
        for key in ("fact_ids", "source_ids"):
            result[key] = sorted(set(before[key]) | set(after[key]))
        return result

    def next_step(self):
        if self._pending is not None: raise ProviderError("TOOL_RESULT_REQUIRED")
        prompt = json.dumps({**self._context, "known_evidence": self._known(),
                             "current_turn_tools": self._history, "corrections": self._feedback},
                            ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(prompt) > 100_000: raise ProviderError("APPLE_INPUT_LIMIT")
        response = self._invoke({"instructions": self._instructions, "prompt": prompt, "plan": self._plan,
                                 "max_output_tokens": self.max_output_tokens})
        self._plan = response["metadata"].get("request_plan", self._plan)
        step, metadata = response.get("decision"), response["metadata"]
        if not isinstance(step, dict) or set(step) != {"type", "value"}: raise ProviderError("APPLE_INVALID_DECISION")
        name, arguments = step["type"], step["value"]
        if not isinstance(arguments, dict): raise ProviderError("APPLE_INVALID_DECISION")
        if name == "finish":
            return Decision(answer=arguments, metadata=metadata)
        if name not in TOOL_FIELDS or set(arguments) - TOOL_FIELDS[name]:
            raise ProviderError("APPLE_INVALID_DECISION")
        arguments = {key: value for key, value in arguments.items() if value is not None}
        call_id = "apple-call-" + uuid.uuid4().hex
        self._pending = call_id
        return Decision(call=ToolCall(call_id, name, json.dumps(arguments)), metadata=metadata)

    def observe(self, call_id, observation):
        if call_id != self._pending: raise ProviderError("TOOL_CALL_ID_MISMATCH")
        self._ledger.ingest(observation)
        result = observation["result"]
        summary = {key: observation[key] for key in ("observation_id", "tool", "arguments")}
        summary["ok"] = result["ok"]
        if not result["ok"]: summary["error"] = result["error"]
        if observation["tool"] == "search_facts" and result["ok"]:
            summary.update({key: result["data"][key] for key in ("count", "truncated")})
        self._history.append(summary)
        self._pending = None

    def feedback(self, message):
        self._feedback.append(message)


if __name__ == "__main__":
    try:
        print(json.dumps(AppleFoundationModelsProvider().public_config(), indent=2))
    except ProviderError as error:
        print(error.code, file=sys.stderr)
        sys.exit(2)
