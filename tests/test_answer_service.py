"""Tests for the streaming AnswerService tool loop."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncio

import pytest

from revops_buddy.conversation import AnswerService, PROVIDER_FAILURE


TOOL_DEFINITION = {
    "name": "get_pipeline_summary",
    "description": "d",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


class FakeRevOps:
    def tool_definitions(self) -> list[dict[str, Any]]:
        return [dict(TOOL_DEFINITION)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "total": "261500"}


class FakeStream:
    def __init__(self, deltas: list[str], content: list[Any], stop_reason: str) -> None:
        self._deltas = deltas
        self._content = content
        self._stop_reason = stop_reason

    @property
    def text_stream(self):
        async def gen():
            for delta in self._deltas:
                yield delta
        return gen()

    async def get_final_message(self):
        return SimpleNamespace(stop_reason=self._stop_reason, content=self._content)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeClient:
    def __init__(self, turns: list[tuple[list[str], list[Any], str]]) -> None:
        self._turns = turns
        self._calls: list[dict[str, Any]] = []

    @property
    def messages(self):
        return self

    @property
    def stream(self):
        client = self

        class StreamFactory:
            def __call__(self, **kwargs):
                client._calls.append(kwargs)
                turn = client._turns.pop(0)
                return FakeStream(*turn)

        return StreamFactory()


def make_service(client: FakeClient) -> AnswerService:
    return AnswerService(
        FakeRevOps(),
        {
            "anthropic_client": client,
            "anthropic_api_key": "k",
            "anthropic_model": "m",
            "bot_name": "Angels",
        },
    )


async def test_sentence_chunking_yields_complete_sentences() -> None:
    client = FakeClient(
        [
            (
                ["Open pipeline is 261,", "500 US dollars. Fourteen", " deals are open."],
                [SimpleNamespace(type="text", text="ignored")],
                "end_turn",
            )
        ]
    )
    service = make_service(client)
    parts = [part async for part in service.stream_answer("what is pipeline?", [])]
    assert parts == ["Open pipeline is 261,500 US dollars.", "Fourteen deals are open."]


async def test_tool_loop_appends_assistant_and_tool_result() -> None:
    tool_call = SimpleNamespace(type="tool_use", id="t1", name="get_pipeline_summary", input={})
    client = FakeClient(
        [
            (["Checking."], [tool_call], "tool_use"),
            (["Pipeline is 261,500 US dollars."], [SimpleNamespace(type="text", text="x")], "end_turn"),
        ]
    )
    service = make_service(client)
    parts = [part async for part in service.stream_answer("pipeline?", [])]
    assert parts == ["Checking.", "Pipeline is 261,500 US dollars."]
    second_messages = client._calls[1]["messages"]
    assistant_msg = second_messages[-2]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == [tool_call]
    tool_msg = second_messages[-1]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"][0]["type"] == "tool_result"
    assert tool_msg["content"][0]["tool_use_id"] == "t1"


async def test_refusal_yields_provider_failure() -> None:
    client = FakeClient([(["Sorry."], [], "refusal")])
    service = make_service(client)
    parts = [part async for part in service.stream_answer("q", [])]
    assert parts == [PROVIDER_FAILURE]


async def test_exception_and_cancellation() -> None:
    class BrokenStream:
        @property
        def text_stream(self):
            async def gen():
                raise RuntimeError("boom")
                yield ""
            return gen()

        async def get_final_message(self): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class BrokenClient(FakeClient):
        def __init__(self) -> None:
            super().__init__([])
            self._calls = []

        @property
        def stream(self):
            return lambda **kwargs: BrokenStream()

    service = make_service(BrokenClient())
    assert [part async for part in service.stream_answer("q", [])] == [PROVIDER_FAILURE]

    class CancelledStream(BrokenStream):
        @property
        def text_stream(self):
            async def gen():
                raise asyncio.CancelledError()
                yield ""
            return gen()

    class CancelledClient(BrokenClient):
        @property
        def stream(self):
            return lambda **kwargs: CancelledStream()

    service = make_service(CancelledClient())
    with pytest.raises(asyncio.CancelledError):
        [part async for part in service.stream_answer("q", [])]


async def test_messages_maps_speaker_history() -> None:
    service = make_service(FakeClient([]))
    messages = service._messages(
        "latest",
        [
            {"speaker": "Angels", "text": "hi"},
            {"speaker": "user", "text": "pipeline"},
            {"role": "assistant", "content": "should stay"},
        ],
    )
    assert messages == [
        {"role": "user", "content": "pipeline"},
        {"role": "assistant", "content": "should stay"},
        {"role": "user", "content": "latest"},
    ]


async def test_request_kwargs_effort_and_cache_control() -> None:
    client = FakeClient([(["Done."], [], "end_turn")])
    service = make_service(client)
    service._settings["anthropic_effort"] = "ultra"
    [part async for part in service.stream_answer("q", [])]
    kwargs = client._calls[0]
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in kwargs
    assert "temperature" not in kwargs

    client = FakeClient([(["Done."], [], "end_turn")])
    service = make_service(client)
    service._settings["anthropic_effort"] = "medium"
    [part async for part in service.stream_answer("q", [])]
    assert client._calls[0]["output_config"] == {"effort": "medium"}


async def test_answer_joins_streamed_sentences() -> None:
    client = FakeClient(
        [
            (
                ["Pipeline is 261,500 US dollars. Fourteen", " deals open."],
                [],
                "end_turn",
            )
        ]
    )
    service = make_service(client)
    assert await service.answer("pipeline?", []) == (
        "Pipeline is 261,500 US dollars. Fourteen deals open."
    )




async def test_slow_provider_degrades_to_speech_instead_of_raising() -> None:
    """A raised TimeoutError would end the whole voice session in the meeting."""

    class SlowStream(FakeStream):
        @property
        def text_stream(self):
            async def gen():
                await asyncio.sleep(5)
                yield "late"
            return gen()

    class SlowClient(FakeClient):
        @property
        def stream(self):
            return lambda **kwargs: SlowStream([], [], "end_turn")

    service = AnswerService(
        FakeRevOps(),
        {
            "anthropic_client": SlowClient([]),
            "anthropic_api_key": "k",
            "anthropic_model": "m",
            "anthropic_timeout_seconds": 1.0,
        },
    )
    assert [part async for part in service.stream_answer("q", [])] == [PROVIDER_FAILURE]
