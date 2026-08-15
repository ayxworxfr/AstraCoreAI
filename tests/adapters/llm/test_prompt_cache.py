"""Prompt-cache breakpoint helpers + Anthropic system/tools/messages wiring."""

from datetime import datetime, timedelta, timezone

from astracore.infrastructure.llm.anthropic import AnthropicAdapter
from astracore.infrastructure.llm.prompt_cache import (
    allocate_message_cache_slots,
    mark_messages_cache_breakpoints,
    mark_tools_cache_breakpoint,
)
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.modules.chat.domain.session_context import SessionContext

_BJ = timezone(timedelta(hours=8))


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


def test_extract_cache_tokens_reads_anthropic_and_deepseek_fields():
    class AnthropicUsage:
        cache_read_input_tokens = 120
        cache_creation_input_tokens = 40

    class DeepSeekUsage:
        prompt_cache_hit_tokens = 88

    assert AnthropicAdapter._extract_cache_tokens(AnthropicUsage()) == (120, 40)
    assert AnthropicAdapter._extract_cache_tokens(DeepSeekUsage()) == (88, 0)
    assert AnthropicAdapter._extract_cache_tokens(None) == (0, 0)


def test_build_system_param_is_static_only():
    system = AnthropicAdapter._build_system_param("STATIC", True)
    assert isinstance(system, list)
    assert system == [{"type": "text", "text": "STATIC", "cache_control": {"type": "ephemeral"}}]
    assert AnthropicAdapter._build_system_param("STATIC", False) == "STATIC"


def test_prepare_request_parts_keeps_prefix_stable_with_session_notes():
    """session 必须挂消息末尾，且不能带 cache_control；历史断点仍在上一轮。"""
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
    assert a.system == b.system
    assert a.system[0]["text"] == "<security>guard</security>"
    assert a.system[0]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(a.system, list) and len(a.system) == 1
    assert a.tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert a.messages[0]["content"][0]["text"] == "hi"
    assert a.messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert a.messages[0] == b.messages[0]
    assert "cache_control" not in a.messages[-1]
    assert "round 1" in a.messages[-1]["content"]
    assert "round 2" in b.messages[-1]["content"]


def test_prepare_request_parts_appends_session_without_cache_flag():
    """DeepSeek 自动前缀缓存：即使不打 cache_control，session 也必须在末尾。"""
    adapter = AnthropicAdapter(api_key="test-key")
    msgs = [
        Message(role=MessageRole.SYSTEM, content="STATIC"),
        Message(role=MessageRole.USER, content="q"),
    ]
    parts = adapter._prepare_cached_request_parts(
        messages=msgs,
        tools=None,
        session_context="<session_context>\nnow\n</session_context>",
        enable_prompt_cache=False,
    )
    assert parts.system == "STATIC"
    assert parts.messages[0] == {"role": "user", "content": "q"}
    assert parts.messages[-1]["role"] == "user"
    assert "now" in parts.messages[-1]["content"]


def test_prepare_request_parts_puts_stable_session_on_system_prefix():
    adapter = AnthropicAdapter(api_key="test-key")
    msgs = [
        Message(role=MessageRole.SYSTEM, content="STATIC"),
        Message(role=MessageRole.USER, content="hi"),
    ]
    ctx = SessionContext.build(
        active_skill="mini-game",
        turn_context="进度：第3题",
        now=datetime(2026, 8, 15, 16, 0, tzinfo=_BJ),
    )
    parts = adapter._prepare_cached_request_parts(
        messages=msgs,
        tools=None,
        session_context=ctx,
        enable_prompt_cache=True,
    )
    assert parts.system[0]["text"] == "STATIC"
    assert parts.system[0]["cache_control"] == {"type": "ephemeral"}
    assert "mini-game" in parts.system[1]["text"]
    assert "cache_control" not in parts.system[1]
    assert "进度：第3题" not in parts.system[1]["text"]
    assert "进度：第3题" in parts.messages[-1]["content"]
    assert parts.messages[0]["content"][0]["text"] == "hi"
    assert parts.messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


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
