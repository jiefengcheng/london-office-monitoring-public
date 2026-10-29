"""Replaceable OpenAI Responses adapter. Standard library only; no auto retry."""
from __future__ import annotations

import copy
from http.client import HTTPException
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import agent_tools
from agent_protocol import ANSWER_SCHEMA, Decision, ProviderError, ToolCall, strict_json

ENDPOINT = "https://api.openai.com/v1/responses"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class OpenAIResponsesProvider:
    def __init__(self, model, api_key, timeout=45, max_output_tokens=4096):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ProviderError("MODEL_NAME_REQUIRED")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderError("API_KEY_REQUIRED")
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ProviderError("INVALID_TIMEOUT")
        if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 16000:
            raise ProviderError("INVALID_OUTPUT_LIMIT")
        self.model = model.strip()
        self._api_key = api_key.strip()
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._opener = build_opener(_NoRedirect())
        self._input = []
        self._pending = None

    def public_config(self):
        return {"provider": "openai", "requested_model": self.model, "endpoint": ENDPOINT,
                "timeout_seconds": self.timeout, "max_output_tokens": self.max_output_tokens,
                "store": False, "parallel_tool_calls": False, "live_model": True}

    def start_turn(self, context, instructions):
        self._input = [{"role": "developer", "content": instructions},
                       {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False)}]
        self._pending = None

    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > 1_000_000:
            raise ProviderError("CONTEXT_LIMIT")
        request = Request(ENDPOINT, data=body, method="POST",
                          headers={"Authorization": "Bearer " + self._api_key,
                                   "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ProviderError("RESPONSE_LIMIT")
            # A response may contain encrypted reasoning; parse but never trace it.
            return strict_json(raw.decode("utf-8"), max_chars=4_000_000)
        except HTTPError as error:
            # Do not copy provider bodies, headers or credentials into errors/traces.
            code = error.code
            error.close()
            raise ProviderError(f"HTTP_{code}") from None
        except (URLError, TimeoutError, socket.timeout, HTTPException, OSError):
            raise ProviderError("NETWORK_ERROR") from None
        except (ValueError, UnicodeError):
            raise ProviderError("INVALID_PROVIDER_JSON") from None

    def next_step(self):
        if self._pending is not None:
            raise ProviderError("TOOL_RESULT_REQUIRED")
        payload = {"model": self.model, "input": copy.deepcopy(self._input),
                   "store": False, "parallel_tool_calls": False,
                   "max_output_tokens": self.max_output_tokens,
                   "tools": [{"type": "function", "name": name, "description": tool["description"],
                              "parameters": tool["parameters"], "strict": False}
                             for name, tool in agent_tools.CONTRACT["tools"].items()],
                   "text": {"format": {"type": "json_schema", "name": "london_answer_selection",
                                       "schema": ANSWER_SCHEMA, "strict": True}}}
        response = self._post(payload)
        if not isinstance(response, dict) or response.get("status") != "completed":
            raise ProviderError("INCOMPLETE_PROVIDER_RESPONSE")
        output = response.get("output")
        if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
            raise ProviderError("INVALID_PROVIDER_OUTPUT")
        if not isinstance(response.get("model"), str) or not response["model"].strip():
            raise ProviderError("MODEL_ID_MISSING")
        usage = response.get("usage") or {}
        if not isinstance(usage, dict) or not isinstance(response.get("id"), str) or not response["id"]:
            raise ProviderError("INVALID_PROVIDER_METADATA")
        metadata = {"response_id": response["id"], "returned_model": response["model"],
                    "usage": {key: value for key, value in usage.items()
                              if key in ("input_tokens", "output_tokens", "total_tokens")
                              and type(value) is int and value >= 0}}
        calls = [item for item in output if item.get("type") == "function_call"]
        if any(item.get("type") == "message" and not isinstance(item.get("content"), list) for item in output):
            raise ProviderError("INVALID_PROVIDER_OUTPUT")
        messages = [part for item in output if item.get("type") == "message"
                    for part in item.get("content", []) if isinstance(part, dict)]
        if any(part.get("type") == "refusal" for part in messages):
            raise ProviderError("MODEL_REFUSAL")
        texts = [part["text"] for part in messages
                 if part.get("type") == "output_text" and isinstance(part.get("text"), str)]
        if len(calls) > 1:
            raise ProviderError("AMBIGUOUS_PROVIDER_OUTPUT")
        metadata["interim_text_ignored"] = bool(calls and texts)
        # Preserve ALL output items within this turn, including reasoning items,
        # before appending function_call_output. Never use previous_response_id.
        self._input.extend(copy.deepcopy(output))
        if calls:
            call = calls[0]
            if not all(isinstance(call.get(k), str) and call[k] for k in ("call_id", "name", "arguments")):
                raise ProviderError("INVALID_FUNCTION_CALL")
            self._pending = call["call_id"]
            return Decision(call=ToolCall(call["call_id"], call["name"], call["arguments"]), metadata=metadata)
        if len(texts) != 1:
            raise ProviderError("ANSWER_MISSING")
        try:
            answer = strict_json(texts[0])
        except ValueError:
            raise ProviderError("INVALID_ANSWER_JSON") from None
        return Decision(answer=answer, metadata=metadata)

    def observe(self, call_id, observation):
        if call_id != self._pending:
            raise ProviderError("TOOL_CALL_ID_MISMATCH")
        self._input.append({"type": "function_call_output", "call_id": call_id,
                            "output": json.dumps(observation, ensure_ascii=False, allow_nan=False)})
        self._pending = None

    def feedback(self, message):
        self._input.append({"role": "user", "content": message})
