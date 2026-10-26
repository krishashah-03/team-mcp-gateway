"""The OpenRouter adapter against a mocked API (no network, no key)."""

import json

import httpx
import pytest

from app.chat import providers
from app.chat.providers import (
    ChatProviderError,
    GeminiProvider,
    OpenRouterProvider,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
)
from app.core.config import settings


def provider_with(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterProvider("or-test-key-123", "vendor/model", client=client)


def reply(message, status=200):
    return httpx.Response(status, json={"choices": [{"message": message, "finish_reason": "stop"}]})


async def generate(provider, turns=None, tools=None):
    return await provider.generate(system="sys", turns=turns or [Turn(role="user", text="hi")], tools=tools or [])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fake(_):
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", fake)


async def test_request_shape_and_text_reply():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return reply({"role": "assistant", "content": "hello"})

    tools = [ToolSpec("utilities__calculate", "Calc", {"type": "object", "properties": {"e": {"type": "string"}}})]
    result = await generate(provider_with(handler), tools=tools)
    assert result.text == "hello" and result.tool_calls == []
    assert seen["auth"] == "Bearer or-test-key-123"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == "vendor/model"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["function"]["name"] == "utilities__calculate"


async def test_fallback_models_are_sent_in_order():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return reply({"content": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider("k", "a/one:free", fallbacks=["b/two:free", "a/one:free", ""], client=client)
    await generate(provider)
    assert seen["body"]["models"] == ["a/one:free", "b/two:free"]


async def test_tool_calls_are_parsed_and_arguments_decoded():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "a__b", "arguments": '{"x": 1}'}},
            {"id": "c2", "type": "function", "function": {"name": "a__c", "arguments": "not json"}},
        ],
    }
    result = await generate(provider_with(lambda r: reply(message)))
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [("c1", "a__b", {"x": 1}), ("c2", "a__c", {})]


async def test_tool_loop_turns_become_openai_messages():
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return reply({"role": "assistant", "content": "done"})

    turns = [
        Turn(role="user", text="add"),
        Turn(role="model", tool_calls=[ToolCall("c1", "utilities__calculate", {"e": "1+1"})]),
        Turn(role="tool", results=[ToolResult("c1", "utilities__calculate", "2")]),
    ]
    await generate(provider_with(handler), turns=turns)
    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert seen["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"e": "1+1"}'
    assert seen["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "2"}


async def test_tool_without_parameters_gets_an_empty_object_schema():
    seen = {}

    def handler(request):
        seen["tools"] = json.loads(request.content)["tools"]
        return reply({"content": "ok"})

    await generate(provider_with(handler), tools=[ToolSpec("s__t", "", {})])
    assert seen["tools"][0]["function"]["parameters"] == {"type": "object", "properties": {}}


async def test_empty_reply_gives_friendly_message():
    result = await generate(provider_with(lambda r: reply({"content": ""})))
    assert "did not return an answer" in result.text


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "API key was rejected"),
        (402, "insufficient credits"),
        (404, "not found or does not support tools"),
        (429, "rate limit"),
        (500, "temporarily unavailable"),
        (400, "rejected the request"),
    ],
)
async def test_http_errors_become_safe_messages(status, message):
    provider = provider_with(lambda r: httpx.Response(status, text="detail with or-test-key-123"))
    with pytest.raises(ChatProviderError, match=message) as excinfo:
        await generate(provider)
    assert "or-test-key-123" not in str(excinfo.value)


async def test_error_inside_a_200_response_is_reported():
    provider = provider_with(lambda r: httpx.Response(200, json={"error": {"message": "upstream down"}}))
    with pytest.raises(ChatProviderError, match="reported an error"):
        await generate(provider)


async def test_one_retry_on_server_error_then_success():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503) if len(attempts) == 1 else reply({"content": "ok"})

    assert (await generate(provider_with(handler))).text == "ok" and len(attempts) == 2


async def test_timeout_and_network_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ChatProviderError, match="in time"):
        await generate(provider_with(slow))
    with pytest.raises(ChatProviderError, match="Could not reach"):
        await generate(provider_with(down))


def test_build_provider_selection():
    base = settings.model_copy(update={"openrouter_api_key": None, "gemini_api_key": None, "llm_provider": "auto"})
    assert providers.build_provider(base) is None
    assert isinstance(providers.build_provider(base.model_copy(update={"openrouter_api_key": "k"})), OpenRouterProvider)
    assert isinstance(providers.build_provider(base.model_copy(update={"gemini_api_key": "k"})), GeminiProvider)
    both = base.model_copy(update={"openrouter_api_key": "k", "gemini_api_key": "g"})
    assert isinstance(providers.build_provider(both), OpenRouterProvider)
    assert isinstance(providers.build_provider(both.model_copy(update={"llm_provider": "gemini"})), GeminiProvider)
    assert providers.build_provider(both.model_copy(update={"llm_provider": "other"})) is None
