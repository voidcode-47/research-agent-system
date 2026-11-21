"""自定义 Anthropic Messages 与 Gemini REST 协议适配。"""
import json
import uuid
from typing import Iterator, Optional
from urllib.parse import quote

import httpx

from llm.base import BaseLLM, ChatResponse, ToolCall


def _function_tools(tools: list[dict] | None) -> list[dict]:
    result = []
    for item in tools or []:
        function = item.get("function", item)
        if function.get("name"):
            result.append(function)
    return result


def _arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def anthropic_messages(messages: list[dict], tools: list[dict] | None, model: str, temperature: float) -> dict:
    system = []
    converted = []
    tool_names = {}
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content") or ""
        if role == "system":
            if content:
                system.append(str(content))
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id", ""))
            name = message.get("name") or tool_names.get(call_id) or "tool"
            blocks = [{
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": str(content),
            }]
            role = "user"
        else:
            blocks = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                blocks.extend(block for block in content if isinstance(block, dict))
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                call_id = str(call.get("id") or uuid.uuid4().hex)
                name = function.get("name", "tool")
                tool_names[call_id] = name
                blocks.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": _arguments(function.get("arguments")),
                })
        if not blocks:
            blocks = [{"type": "text", "text": ""}]
        converted.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})

    # Messages API expects alternating user/assistant turns. Merge adjacent roles,
    # which can occur when callers return multiple tool results independently.
    merged = []
    for item in converted:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"].extend(item["content"])
        else:
            merged.append(item)
    payload = {"model": model, "messages": merged, "max_tokens": 4096, "temperature": temperature}
    if system:
        payload["system"] = "\n\n".join(system)
    declarations = [
        {
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        }
        for function in _function_tools(tools)
    ]
    if declarations:
        payload["tools"] = declarations
    return payload


def gemini_contents(messages: list[dict], tools: list[dict] | None, temperature: float) -> dict:
    system = []
    contents = []
    tool_names = {}
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content") or ""
        if role == "system":
            if content:
                system.append(str(content))
            continue
        parts = []
        if role == "tool":
            call_id = str(message.get("tool_call_id", ""))
            name = message.get("name") or tool_names.get(call_id) or "tool"
            parts.append({"functionResponse": {"name": name, "response": {"result": content}}})
            role = "user"
        else:
            if isinstance(content, str) and content:
                parts.append({"text": content})
            elif isinstance(content, list):
                parts.extend({"text": str(part.get("text", ""))} for part in content if isinstance(part, dict) and part.get("text"))
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                name = function.get("name", "tool")
                call_id = str(call.get("id") or uuid.uuid4().hex)
                tool_names[call_id] = name
                parts.append({"functionCall": {"name": name, "args": _arguments(function.get("arguments"))}})
        if not parts:
            parts = [{"text": ""}]
        contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    merged = []
    for item in contents:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["parts"].extend(item["parts"])
        else:
            merged.append(item)
    payload = {
        "contents": merged,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    declarations = [
        {"name": function["name"], "description": function.get("description", ""),
         "parameters": function.get("parameters") or {"type": "object", "properties": {}}}
        for function in _function_tools(tools)
    ]
    if declarations:
        payload["tools"] = [{"functionDeclarations": declarations}]
    return payload


def parse_anthropic_response(data: dict) -> ChatResponse:
    text = []
    tool_calls = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                id=str(block.get("id") or uuid.uuid4().hex),
                name=str(block.get("name") or "tool"),
                arguments=_arguments(block.get("input")),
            ))
    usage = data.get("usage") or {}
    prompt = int(usage.get("input_tokens", 0))
    completion = int(usage.get("output_tokens", 0))
    return ChatResponse("\n".join(text), tool_calls, {
        "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
    }, raw=data, finish_reason=str(data.get("stop_reason") or ""))


def parse_gemini_response(data: dict) -> ChatResponse:
    text = []
    tool_calls = []
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
    for part in parts:
        if part.get("text"):
            text.append(part["text"])
        function = part.get("functionCall")
        if function:
            tool_calls.append(ToolCall(
                id=str(function.get("id") or uuid.uuid4().hex),
                name=str(function.get("name") or "tool"),
                arguments=_arguments(function.get("args")),
            ))
    usage = data.get("usageMetadata") or {}
    prompt = int(usage.get("promptTokenCount", 0))
    completion = int(usage.get("candidatesTokenCount", 0))
    total = int(usage.get("totalTokenCount", prompt + completion))
    return ChatResponse("\n".join(text), tool_calls, {
        "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total,
    }, raw=data, finish_reason=str(candidates[0].get("finishReason") or "") if candidates else "")


class CustomProtocolLLM(BaseLLM):
    def __init__(self, protocol: str, api_key: str, base_url: str, model: str, provider: str):
        super().__init__(model=model, provider=provider)
        self.protocol = protocol
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=60.0, follow_redirects=False)

    def _anthropic_url(self, suffix: str) -> str:
        base = self.base_url.removesuffix("/v1/messages").removesuffix("/v1")
        return f"{base}/v1/{suffix.lstrip('/')}"

    def _gemini_url(self, action: str = "generateContent") -> str:
        base = self.base_url.removesuffix("/v1beta")
        return f"{base}/v1beta/models/{quote(self.model, safe='') }:{action}"

    def _request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if self.protocol == "anthropic":
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        elif self.protocol == "gemini":
            headers["x-goog-api-key"] = self.api_key
        if "json" in kwargs:
            headers["content-type"] = "application/json"
        response = self.client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        try:
            self.client.close()
        except Exception:
            pass

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None, temperature: float = 0.7, **options) -> ChatResponse:
        if self.protocol == "anthropic":
            payload = anthropic_messages(messages, tools, self.model, temperature)
            if options.get("max_tokens"):
                payload["max_tokens"] = options["max_tokens"]
            response = self._request("POST", self._anthropic_url("messages"), json=payload,
                                     timeout=options.get("timeout", 60.0))
            return parse_anthropic_response(response.json())
        payload = gemini_contents(messages, tools, temperature)
        if options.get("max_tokens"):
            payload["generationConfig"]["maxOutputTokens"] = options["max_tokens"]
        response = self._request("POST", self._gemini_url(), json=payload,
                                 timeout=options.get("timeout", 60.0))
        return parse_gemini_response(response.json())

    def stream_chat(self, messages: list[dict], tools: Optional[list[dict]] = None, temperature: float = 0.7, **options) -> Iterator[str]:
        if self.protocol == "anthropic":
            payload = anthropic_messages(messages, tools, self.model, temperature)
            payload["stream"] = True
            if options.get("max_tokens"):
                payload["max_tokens"] = options["max_tokens"]
            url = self._anthropic_url("messages")
        else:
            payload = gemini_contents(messages, tools, temperature)
            payload["generationConfig"]["maxOutputTokens"] = options.get("max_tokens", 4096)
            url = self._gemini_url("streamGenerateContent") + "?alt=sse"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"} if self.protocol == "anthropic" else {"x-goog-api-key": self.api_key}
        finished = False
        reason = ""
        with self.client.stream("POST", url, json=payload, headers=headers,
                                timeout=options.get("timeout", 60.0)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError as exc:
                    raise RuntimeError("模型流式响应包含无效 JSON") from exc
                if not isinstance(data, dict):
                    raise RuntimeError("模型流式响应不是对象")
                if data.get("error") or data.get("type") == "error":
                    raise RuntimeError("模型流式服务返回错误")
                if self.protocol == "anthropic":
                    delta = data.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")
                    if delta.get("stop_reason"):
                        reason = delta["stop_reason"]
                    if data.get("type") == "message_stop":
                        finished = True
                else:
                    candidates = data.get("candidates") or []
                    candidate = candidates[0] if candidates else {}
                    for part in (candidate.get("content") or {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]
                    if candidate.get("finishReason"):
                        reason = candidate["finishReason"]
                        finished = True
            allowed = ("end_turn", "stop_sequence") if self.protocol == "anthropic" else ("STOP",)
            if not finished or reason not in allowed:
                raise RuntimeError(f"模型流式响应未完整结束（{reason or '连接中断'}）")

    def ping(self) -> bool:
        try:
            if self.protocol == "anthropic":
                url = self._anthropic_url(f"models/{quote(self.model, safe='')}")
            else:
                url = f"{self.base_url.removesuffix('/v1beta')}/v1beta/models"
            self._request("GET", url)
            return True
        except Exception:
            return False

    def list_models(self) -> list[str]:
        return []

    def health_check(self) -> dict:
        reachable = self.ping()
        return {
            "reachable": reachable, "models_loaded": reachable, "model_ready": reachable,
            "available_models": [],
            "detail": f"就绪，当前模型: {self.model}" if reachable else f"无法连接 {self.base_url}，请检查 API Key、地址或模型 ID",
        }
