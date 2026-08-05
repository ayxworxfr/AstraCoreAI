"""Prompt-cache breakpoint helpers + Anthropic system/tools/messages wiring."""

from astracore.infrastructure.llm.anthropic import AnthropicAdapter
from astracore.infrastructure.llm.prompt_cache import (
    allocate_message_cache_slots,
    mark_messages_cache_breakpoints,
    mark_tools_cache_breakpoint,
)
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult


def test_mark_tools_cache_breakpoint_on_last_tool_only():
    tools = [
        {"name": "a", "description": "A", "input_schema": {"type": "object", "properties": {}}},
        {"name": "b", "description": "B", "input_schema": {"type": "object", "properties": {}}},
    ]
    marked = mark_tools_cache_breakpoint(tools)
    assert marked is not None
    assert "cache_control" not in marked[0]
    assert marked[1]["cache_control"] == {"type": "ephemeral"}
    # 不修改入参
    assert "cache_control" not in tools[1]


def test_mark_tools_cache_breakpoint_noop_on_empty():
    assert mark_tools_cache_breakpoint(None) is None
    assert mark_tools_cache_breakpoint([]) == []


def test_allocate_message_slots_respects_four_breakpoint_cap():
    assert allocate_message_cache_slots(has_tools=True, has_cached_system=True) == 2
    assert allocate_message_cache_slots(has_tools=False, has_cached_system=True) == 3
    assert allocate_message_cache_slots(has_tools=False, has_cached_system=False) == 4


def test_mark_messages_places_breakpoint_on_final_block():
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "calling"},
                {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "ok",
                    "is_error": False,
                }
            ],
        },
    ]
    out = mark_messages_cache_breakpoints(messages, remaining_slots=2)
    last_block = out[-1]["content"][-1]
    assert last_block["cache_control"] == {"type": "ephemeral"}
    # 字符串 content 被规范为 text block 以便挂断点
    assert isinstance(out[0]["content"], list)
    assert out[0]["content"][0]["type"] == "text"


def test_mark_messages_adds_intermediate_breakpoints_for_long_prefix():
    # 16 个 user 文本块 → 需要中间断点，避免超过 20-block lookback
    messages = [{"role": "user", "content": f"m{i}"} for i in range(16)]
    out = mark_messages_cache_breakpoints(messages, remaining_slots=2)
    marked = [
        (i, b)
        for i, msg in enumerate(out)
        for b in msg["content"]
        if isinstance(b, dict) and "cache_control" in b
    ]
    assert len(marked) == 2
    assert marked[-1][0] == 15  # 最后一条必有断点


def test_build_system_param_caches_static_not_session():
    system = AnthropicAdapter._build_system_param(
        "STATIC",
        "<session_context>\n<datetime/>\n</session_context>",
        True,
    )
    assert isinstance(system, list)
    assert system[0]["text"] == "STATIC"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]


def test_prepare_request_parts_keeps_cached_system_stable_with_session_notes():
    """session 侧动态文案不得污染带 cache_control 的 system block。"""
    adapter = AnthropicAdapter(api_key="test-key")
    msgs = [
        Message(role=MessageRole.SYSTEM, content="<security>guard</security>"),
        Message(role=MessageRole.USER, content="hi"),
    ]
    tools = [
        {
            "name": "search",
            "description": "s",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]
    a = adapter._prepare_cached_request_parts(
        messages=msgs,
        tools=tools,
        session_context="<session_context>\nround 1\n</session_context>",
        enable_prompt_cache=True,
    )
    b = adapter._prepare_cached_request_parts(
        messages=msgs,
        tools=tools,
        session_context="<session_context>\nround 2\n</session_context>",
        enable_prompt_cache=True,
    )
    assert a.system[0]["text"] == b.system[0]["text"] == "<security>guard</security>"
    assert a.system[0]["cache_control"] == b.system[0]["cache_control"]
    assert a.system[1]["text"] != b.system[1]["text"]
    assert a.tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert a.messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_prepare_request_parts_message_breakpoint_moves_with_history():
    adapter = AnthropicAdapter(api_key="test-key")
    tc = ToolCall(id="tc_1", name="search", arguments={"q": "x"})
    tr = ToolResult(tool_call_id="tc_1", name="search", content="result")
    short = [
        Message(role=MessageRole.SYSTEM, content="STATIC"),
        Message(role=MessageRole.USER, content="q1"),
    ]
    longer = [
        *short,
        Message(role=MessageRole.ASSISTANT, content="", tool_calls=[tc]),
        Message(role=MessageRole.TOOL, content="", tool_results=[tr]),
    ]
    a = adapter._prepare_cached_request_parts(
        messages=short, tools=None, session_context=None, enable_prompt_cache=True
    )
    b = adapter._prepare_cached_request_parts(
        messages=longer, tools=None, session_context=None, enable_prompt_cache=True
    )
    assert a.messages[-1]["content"][-1]["text"] == "q1"
    assert a.messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert b.messages[-1]["content"][-1]["type"] == "tool_result"
    assert b.messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
