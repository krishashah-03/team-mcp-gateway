"""LLM provider adapters for the chat agent.

The agent talks to a small provider-neutral interface, so swapping Gemini for another
model means writing one adapter and nothing else. Implemented: OpenRouter and Gemini.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ChatProviderError(Exception):
    """A failure of the model service, with a message safe to show to the user."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class Turn:
    role: Literal["user", "model", "tool"]
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    # Provider-native payload to send back unchanged on the next request. Some models
    # attach opaque state (e.g. Gemini thought signatures) that must be echoed.
    raw: Any = None


@dataclass
class ModelReply:
    text: str | None
    tool_calls: list[ToolCall]
    raw: Any = None


class LLMProvider(Protocol):
    async def generate(
        self, *, system: str, turns: list[Turn], tools: list[ToolSpec]
    ) -> ModelReply: ...


# ----- Gemini ---------------------------------------------------------------------

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Gemini accepts only a subset of JSON Schema; anything else is a 400 error.
_ALLOWED_SCHEMA_KEYS = {"type", "format", "description", "nullable", "enum", "items", "properties", "required"}


def to_gemini_schema(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    """Reduce a JSON Schema to the subset Gemini's function declarations accept."""
    if not isinstance(schema, dict):
        return {"type": "STRING"}
    defs = defs if defs is not None else {**schema.get("$defs", {}), **schema.get("definitions", {})}

    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.rsplit("/", 1)[-1] in defs:
        return to_gemini_schema(defs[ref.rsplit("/", 1)[-1]], defs)

    nullable = False
    for key in ("anyOf", "oneOf"):
        if key in schema:
            branches = [b for b in schema[key] if isinstance(b, dict)]
            non_null = [b for b in branches if b.get("type") != "null"]
            nullable = len(non_null) < len(branches)
            merged = {k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}
            merged.update(non_null[0] if non_null else {"type": "string"})
            schema = merged
            break

    declared_type = schema.get("type")
    if isinstance(declared_type, list):
        non_null_types = [t for t in declared_type if t != "null"]
        nullable = nullable or len(non_null_types) < len(declared_type)
        declared_type = non_null_types[0] if non_null_types else "string"

    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "type":
            continue
        if key == "properties" and isinstance(value, dict):
            result[key] = {name: to_gemini_schema(spec, defs) for name, spec in value.items()}
        elif key == "items":
            result[key] = to_gemini_schema(value, defs)
        elif key == "enum" and isinstance(value, list):
            result[key] = [str(v) for v in value]
        else:
            result[key] = value
    result["type"] = str(declared_type or ("OBJECT" if "properties" in schema else "STRING")).upper()
    if nullable:
        result["nullable"] = True
    if "required" in result and isinstance(result.get("properties"), dict):
        result["required"] = [r for r in result["required"] if r in result["properties"]]
        if not result["required"]:
            del result["required"]
    return result


def _function_declaration(tool: ToolSpec) -> dict[str, Any]:
    declaration: dict[str, Any] = {"name": tool.name, "description": tool.description[:1500] or tool.name}
    parameters = to_gemini_schema(tool.parameters)
    # Gemini rejects an OBJECT with no properties, so a no-argument tool omits parameters.
    if parameters.get("properties"):
        declaration["parameters"] = parameters
    return declaration


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client = client

    def _contents(self, turns: list[Turn]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for turn in turns:
            if turn.role == "user":
                contents.append({"role": "user", "parts": [{"text": turn.text or ""}]})
            elif turn.role == "model":
                if turn.raw is not None:
                    contents.append(turn.raw)
                    continue
                parts: list[dict[str, Any]] = []
                if turn.text:
                    parts.append({"text": turn.text})
                parts.extend(
                    {"functionCall": {"name": c.name, "args": c.arguments}} for c in turn.tool_calls
                )
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            else:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": r.name,
                                    "response": {"error": r.content} if r.is_error else {"result": r.content},
                                }
                            }
                            for r in turn.results
                        ],
                    }
                )
        return contents

    async def generate(
        self, *, system: str, turns: list[Turn], tools: list[ToolSpec]
    ) -> ModelReply:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(turns),
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [_function_declaration(t) for t in tools]}]

        payload = await self._post(body)
        candidates = payload.get("candidates") or []
        content = candidates[0].get("content") if candidates else None
        parts = (content or {}).get("parts") or []

        text = "".join(p["text"] for p in parts if isinstance(p.get("text"), str) and not p.get("thought"))
        calls = [
            ToolCall(
                id=f"call_{index}",
                name=part["functionCall"]["name"],
                arguments=part["functionCall"].get("args") or {},
            )
            for index, part in enumerate(p for p in parts if "functionCall" in p)
        ]
        if not text and not calls:
            reason = (candidates[0].get("finishReason") if candidates else None) or "no content"
            logger.warning("Gemini returned no usable content (%s)", reason)
            text = "The model did not return an answer. Try rephrasing your question."
        return ModelReply(text=text or None, tool_calls=calls, raw=content if calls else None)

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = _GEMINI_URL.format(model=self._model)
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            for attempt in (1, 2):
                try:
                    response = await client.post(url, json=body, headers=headers, timeout=self._timeout)
                except httpx.TimeoutException:
                    raise ChatProviderError("The model did not respond in time. Try again.") from None
                except httpx.RequestError:
                    raise ChatProviderError("Could not reach the model service.") from None

                if (response.status_code == 429 or response.status_code >= 500) and attempt == 1:
                    await asyncio.sleep(2.0)
                    continue
                break
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code in (401, 403):
            raise ChatProviderError("The Gemini API key was rejected. Check GEMINI_API_KEY.")
        if response.status_code == 429:
            raise ChatProviderError("The Gemini rate limit was reached. Wait a moment and retry.")
        if response.status_code == 404:
            raise ChatProviderError(f"Gemini model '{self._model}' was not found. Check GEMINI_MODEL.")
        if response.status_code >= 500:
            logger.error("Gemini returned %s: %s", response.status_code, response.text[:500])
            raise ChatProviderError("The Gemini service is temporarily unavailable.")
        if response.status_code >= 400:
            # Log the provider's message for the operator; do not show it to end users.
            logger.error("Gemini rejected the request: %s", response.text[:500])
            raise ChatProviderError("The model rejected the request.")
        try:
            return response.json()
        except ValueError:
            raise ChatProviderError("The model returned an unreadable response.") from None


# ----- OpenRouter (OpenAI-compatible chat completions) ------------------------------

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        fallbacks: list[str] | None = None,
        timeout: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._fallbacks = [m for m in (fallbacks or []) if m and m != model]
        self._timeout = timeout
        self._client = client

    @staticmethod
    def _tool_definition(tool: ToolSpec) -> dict[str, Any]:
        parameters = tool.parameters if isinstance(tool.parameters, dict) else {}
        if parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description[:1500] or tool.name,
                "parameters": parameters,
            },
        }

    @staticmethod
    def _messages(system: str, turns: list[Turn]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in turns:
            if turn.role == "user":
                messages.append({"role": "user", "content": turn.text or ""})
            elif turn.role == "model":
                message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
                if turn.tool_calls:
                    message["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in turn.tool_calls
                    ]
                elif not turn.text:
                    message["content"] = ""
                messages.append(message)
            else:
                messages.extend(
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.content or ("error" if r.is_error else "")}
                    for r in turn.results
                )
        return messages

    async def generate(
        self, *, system: str, turns: list[Turn], tools: list[ToolSpec]
    ) -> ModelReply:
        body: dict[str, Any] = {"model": self._model, "messages": self._messages(system, turns)}
        if self._fallbacks:
            body["models"] = [self._model, *self._fallbacks]
        if tools:
            body["tools"] = [self._tool_definition(t) for t in tools]

        payload = await self._post(body)
        choices = payload.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {}

        text = message.get("content")
        text = text if isinstance(text, str) else ""
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=call.get("id") or f"call_{index}",
                    name=function.get("name") or "",
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        if not text and not calls:
            reason = (choices[0].get("finish_reason") if choices else None) or "no content"
            logger.warning("OpenRouter returned no usable content (%s)", reason)
            text = "The model did not return an answer. Try rephrasing your question."
        return ModelReply(text=text or None, tool_calls=calls)

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": "Team MCP Gateway",
        }
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            for attempt in (1, 2):
                try:
                    response = await client.post(_OPENROUTER_URL, json=body, headers=headers, timeout=self._timeout)
                except httpx.TimeoutException:
                    raise ChatProviderError("The model did not respond in time. Try again.") from None
                except httpx.RequestError:
                    raise ChatProviderError("Could not reach the model service.") from None

                if (response.status_code in (408, 429) or response.status_code >= 500) and attempt == 1:
                    await asyncio.sleep(2.0)
                    continue
                break
        finally:
            if self._client is None:
                await client.aclose()

        status_code = response.status_code
        if status_code in (401, 403):
            raise ChatProviderError("The OpenRouter API key was rejected. Check OPENROUTER_API_KEY.")
        if status_code == 402:
            raise ChatProviderError("OpenRouter reports insufficient credits for this model.")
        if status_code == 429:
            raise ChatProviderError("The model rate limit was reached. Wait a moment and retry.")
        if status_code == 404:
            logger.error("OpenRouter 404: %s", response.text[:500])
            raise ChatProviderError(
                f"Model '{self._model}' was not found or does not support tools. Check OPENROUTER_MODEL."
            )
        if status_code >= 500:
            logger.error("OpenRouter returned %s: %s", status_code, response.text[:500])
            raise ChatProviderError("The model service is temporarily unavailable.")
        if status_code >= 400:
            logger.error("OpenRouter rejected the request: %s", response.text[:500])
            raise ChatProviderError("The model rejected the request.")
        try:
            payload = response.json()
        except ValueError:
            raise ChatProviderError("The model returned an unreadable response.") from None
        if not isinstance(payload, dict):
            raise ChatProviderError("The model returned an unreadable response.")
        if payload.get("error") and not payload.get("choices"):
            # OpenRouter can report upstream provider failures inside a 200 response.
            logger.error("OpenRouter error: %s", str(payload["error"])[:500])
            raise ChatProviderError("The model service reported an error. Try again.")
        return payload


def build_provider(settings: Settings) -> LLMProvider | None:
    """The configured provider, or None when no API key is set."""
    timeout = min(45.0, settings.chat_timeout_seconds)
    choice = settings.llm_provider
    if choice in ("auto", "openrouter") and settings.openrouter_api_key:
        fallbacks = [m.strip() for m in settings.openrouter_fallback_models.split(",") if m.strip()]
        return OpenRouterProvider(
            settings.openrouter_api_key, settings.openrouter_model, fallbacks=fallbacks, timeout=timeout
        )
    if choice in ("auto", "gemini") and settings.gemini_api_key:
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, timeout=timeout)
    return None
