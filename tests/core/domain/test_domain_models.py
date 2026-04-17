"""Test that domain models use timezone-aware datetimes."""
from astracore.core.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.core.domain.session import SessionState


def test_message_created_at_is_timezone_aware():
    msg = Message(role=MessageRole.USER, content="hi")
    assert msg.created_at.tzinfo is not None


def test_tool_call_created_at_is_timezone_aware():
    tc = ToolCall(name="test", arguments={})
    assert tc.created_at.tzinfo is not None


def test_session_created_at_is_timezone_aware():
    session = SessionState()
    assert session.created_at.tzinfo is not None
    assert session.updated_at.tzinfo is not None
