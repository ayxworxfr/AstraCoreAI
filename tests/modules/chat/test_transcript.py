"""Transcript expand / rebuild round-trip."""

from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.modules.chat.domain.transcript import entries_to_messages, message_to_entries


def test_tool_trajectory_round_trip():
    assistant = Message(
        role=MessageRole.ASSISTANT,
        content="looking",
        tool_calls=[ToolCall(name="recall_memory", arguments={"query": "prefs"})],
    )
    tool = Message(
        role=MessageRole.TOOL,
        content="",
        tool_results=[
            ToolResult(
                tool_call_id=assistant.tool_calls[0].id,
                name="recall_memory",
                content="likes dark mode",
            )
        ],
    )
    entries = message_to_entries(assistant) + message_to_entries(tool)
    rebuilt = entries_to_messages(entries)

    assert rebuilt[0].role == MessageRole.ASSISTANT
    assert rebuilt[0].tool_calls[0].name == "recall_memory"
    assert rebuilt[1].role == MessageRole.TOOL
    assert rebuilt[1].tool_results[0].content == "likes dark mode"


def test_compact_entry_rebuilds_as_user():
    msg = Message(
        role=MessageRole.USER,
        content="[记忆同步]\nsummary",
        metadata={"compacted": True, "synthetic": True},
    )
    entries = message_to_entries(msg)
    assert entries[0].kind.value == "compact"
    rebuilt = entries_to_messages(entries)
    assert rebuilt[0].metadata.get("compacted") is True
