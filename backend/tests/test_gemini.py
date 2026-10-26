"""The Gemini adapter, tested against a mocked HTTP API (no key, no network)."""

import json

import httpx
import pytest

from app.chat import providers
from app.chat.providers import (
    ChatProviderError,
    GeminiProvider,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    to_gemini_schema,
)


class TestSchemaConversion:
    def test_optional_fields_become_nullable(self):
        schema = {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Description", "default": None}
        assert to_gemini_schema(schema) == {"type": "STRING", "nullable": True}

    def test_unsupported_keywords_are_dropped(self):
        schema = {
            "type": "object", "title": "T", "additionalProperties": False, "$schema": "x",
            "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3, "description": "count"}},
            "required": ["n"],
        }
        assert to_gemini_schema(schema) == {
            "type": "OBJECT",
            "properties": {"n": {"type": "INTEGER", "description": "count"}},
            "required": ["n"],
        }

    def test_type_list_with_null(self):
        assert to_gemini_schema({"type": ["integer", "null"]}) == {"type": "INTEGER", "nullable": True}

    def test_refs_are_inlined(self):
        schema = {
            "type": "object",
            "properties": {"item": {"$ref": "#/$defs/Item"}},
            "$defs": {"Item": {"type": "object", "properties": {"id": {"type": "integer"}}}},
        }
        assert to_gemini_schema(schema)["properties"]["item"] == {
            "type": "OBJECT", "properties": {"id": {"type": "INTEGER"}}
        }

    def test_arrays_and_enums(self):
        schema = {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}
        assert to_gemini_schema(schema) == {"type": "ARRAY", "items": {"type": "STRING", "enum": ["a", "b"]}}

    def test_required_names_not_in_properties_are_removed(self):
        assert "required" not in to_gemini_schema({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["ghost"]})

    def test_non_dict_schema_does_not_crash(self):
        assert to_gemini_schema(None) == {"type": "STRING"}


def provider_with(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiProvider("test-key-123", "gemini-test", client=client, **kwargs)


def reply_with(parts, status=200):
    return httpx.Response(status, json={"candidates": [{"content": {"role": "model", "parts": parts}}]})


async def generate(provider, turns=None, tools=None):
    return await provider.generate(system="sys", turns=turns or [Turn(role="user", text="hi")], tools=tools or [])


async def test_plain_answer_and_request_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return reply_with([{"text": "Hello there"}])

    reply = await generate(
        provider_with(handler),
        tools=[ToolSpec("shop__list_orders", "List orders", {"type": "object", "properties": {"after": {"type": "string"}}})],
    )
    assert reply.text == "Hello there" and reply.tool_calls == []
    assert "gemini-test:generateContent" in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "test-key-123"
    assert "test-key-123" not in seen["url"]  # the key travels in a header, never the URL
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["body"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    declaration = seen["body"]["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "shop__list_orders"
    assert declaration["parameters"]["properties"]["after"] == {"type": "STRING"}


async def test_tool_without_arguments_omits_parameters():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return reply_with([{"text": "ok"}])

    await generate(provider_with(handler), tools=[ToolSpec("t", "d", {"type": "object", "properties": {}})])
    assert "parameters" not in seen["body"]["tools"][0]["functionDeclarations"][0]


async def test_function_calls_are_parsed_and_raw_content_is_kept():
    parts = [
        {"functionCall": {"name": "shop__list_orders", "args": {"after": "2026-10-23"}}, "thoughtSignature": "opaque"},
        {"functionCall": {"name": "utilities__current_time", "args": {}}},
    ]
    reply = await generate(provider_with(lambda r: reply_with(parts)))
    assert [(c.name, c.arguments) for c in reply.tool_calls] == [
        ("shop__list_orders", {"after": "2026-10-23"}), ("utilities__current_time", {}),
    ]
    assert reply.raw["parts"][0]["thoughtSignature"] == "opaque"  # echoed back on the next request


async def test_raw_model_turn_and_tool_results_are_replayed():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return reply_with([{"text": "done"}])

    raw = {"role": "model", "parts": [{"functionCall": {"name": "t", "args": {}}, "thoughtSignature": "sig"}]}
    turns = [
        Turn(role="user", text="q"),
        Turn(role="model", tool_calls=[ToolCall("c0", "t", {})], raw=raw),
        Turn(role="tool", results=[ToolResult("c0", "t", "result text"), ToolResult("c1", "u", "boom", is_error=True)]),
    ]
    await generate(provider_with(handler), turns=turns)
    contents = seen["body"]["contents"]
    assert contents[1] == raw
    assert contents[2]["parts"][0]["functionResponse"] == {"name": "t", "response": {"result": "result text"}}
    assert contents[2]["parts"][1]["functionResponse"]["response"] == {"error": "boom"}


async def test_thought_parts_are_not_shown_as_the_answer():
    reply = await generate(provider_with(lambda r: reply_with([{"text": "private reasoning", "thought": True}, {"text": "Visible answer"}])))
    assert reply.text == "Visible answer"


@pytest.mark.parametrize("payload", [{}, {"candidates": []}, {"candidates": [{"finishReason": "SAFETY"}]}])
async def test_empty_responses_give_a_friendly_message(payload):
    reply = await generate(provider_with(lambda r: httpx.Response(200, json=payload)))
    assert "did not return an answer" in reply.text


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "API key was rejected"),
        (403, "API key was rejected"),
        (404, "was not found"),
        (500, "temporarily unavailable"),
        (503, "temporarily unavailable"),
        (400, "rejected the request"),
    ],
)
async def test_http_errors_become_safe_messages(status, message):
    secret_detail = "internal detail with key test-key-123"
    provider = provider_with(lambda r: httpx.Response(status, text=secret_detail))
    with pytest.raises(ChatProviderError, match=message) as excinfo:
        await generate(provider)
    assert "test-key-123" not in str(excinfo.value)


async def test_rate_limit_retries_once_then_succeeds(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", no_sleep)
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(429) if len(attempts) == 1 else reply_with([{"text": "ok"}])

    assert (await generate(provider_with(handler))).text == "ok" and len(attempts) == 2


async def test_rate_limit_gives_up_after_one_retry(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", no_sleep)
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(429)

    with pytest.raises(ChatProviderError, match="rate limit"):
        await generate(provider_with(handler))
    assert len(attempts) == 2


async def test_timeout_and_network_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ChatProviderError, match="did not respond"):
        await generate(provider_with(slow))
    with pytest.raises(ChatProviderError, match="Could not reach"):
        await generate(provider_with(down))


async def test_unreadable_body():
    with pytest.raises(ChatProviderError, match="unreadable"):
        await generate(provider_with(lambda r: httpx.Response(200, text="<html>not json</html>")))


def test_provider_is_only_built_when_a_key_is_configured():
    from app.core.config import settings

    assert providers.build_provider(settings.model_copy(update={"gemini_api_key": None})) is None
    assert providers.build_provider(settings.model_copy(update={"gemini_api_key": "k"})) is not None
    assert providers.build_provider(settings.model_copy(update={"llm_provider": "other", "gemini_api_key": "k"})) is None
