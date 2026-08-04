"""Native 工具结果不得把执行 context 写入 metadata（会污染 transcript JSON）。"""

from __future__ import annotations

import json

import pytest

from astracore.infrastructure.tools.native import NativeToolAdapter
from astracore.modules.chat.domain.message import Message, MessageRole, ToolResult
from astracore.modules.chat.domain.transcript import message_to_entries
from astracore.modules.tools.ports.tool import ToolParameter, ToolParameterType


@pytest.mark.asyncio
async def test_native_success_metadata_excludes_execution_context():
    adapter = NativeToolAdapter()
    adapter.register_tool(
        name="echo",
        func=lambda text: text,
        description="echo",
        parameters=[
            ToolParameter(
                name="text",
                type=ToolParameterType.STRING,
                description="t",
                required=True,
            )
        ],
        is_concurrency_safe=True,
        is_readonly=True,
    )
    ctx = {
        "allowed_tools": frozenset({"echo"}),
        "_read_files": set(),
        "user_id": "u1",
    }
    result = await adapter.execute("echo", {"text": "hi"}, context=ctx)
    assert result.ok is True
    assert "context" not in result.metadata
    # 即使历史路径误把 context 塞进 ToolResult，transcript 边界也必须可序列化
    msg = Message(
        role=MessageRole.TOOL,
        content="",
        tool_results=[
            ToolResult(
                tool_call_id="1",
                name="echo",
                content="hi",
                metadata={"context": ctx},
            )
        ],
    )
    entry = message_to_entries(msg)[0]
    json.dumps(entry.metadata)
    assert isinstance(entry.metadata["context"]["allowed_tools"], list)
