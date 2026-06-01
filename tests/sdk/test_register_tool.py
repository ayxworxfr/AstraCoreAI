"""Tests for SDK tool registration and lifecycle."""

import inspect

import pytest

from astracore.infrastructure.tools.composite import CompositeToolAdapter
from astracore.infrastructure.tools.native import NativeToolAdapter
from astracore.sdk.client import AstraCoreClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user_adapter_with_tool() -> NativeToolAdapter:
    adapter = NativeToolAdapter()
    adapter.register_tool(
        name="my_custom_tool",
        func=lambda: "ok",
        description="A user-registered test tool",
        parameters=[],
    )
    return adapter


# ---------------------------------------------------------------------------
# Unit: CompositeToolAdapter preserves registered tools after MCP startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_tools_survive_mcp_startup():
    """Tools registered into _user_adapter must remain visible after MCP wraps the composite."""
    builtin = NativeToolAdapter()  # stand-in for build_tool_adapter()
    user = NativeToolAdapter()
    mcp = NativeToolAdapter()  # stand-in for MCPToolAdapter
    mcp.register_tool(
        name="mcp_tool",
        func=lambda: "mcp",
        description="An MCP tool",
        parameters=[],
    )

    # Pre-MCP state: composite holds builtin + user
    pre_mcp = CompositeToolAdapter([builtin, user])
    pre_mcp.register_tool(
        name="my_custom_tool",
        func=lambda: "ok",
        description="User tool",
        parameters=[],
    )

    # MCP starts: new composite, but _same_ user adapter instance
    post_mcp = CompositeToolAdapter([builtin, user, mcp])

    names = {d.name for d in post_mcp.get_definitions()}
    assert "my_custom_tool" in names, "User-registered tool lost after MCP startup"
    assert "mcp_tool" in names


@pytest.mark.asyncio
async def test_tools_registered_after_mcp_startup_are_executable():
    """Tools added after the composite is built (via dynamic routing) must be executable."""
    builtin = NativeToolAdapter()
    user = NativeToolAdapter()
    composite = CompositeToolAdapter([builtin, user])

    # Simulate registering AFTER the composite was already built
    user.register_tool(
        name="late_tool",
        func=lambda: "late_result",
        description="Registered after composite construction",
        parameters=[],
    )

    defs = {d.name for d in composite.get_definitions()}
    assert "late_tool" in defs

    result = await composite.execute("late_tool", {})
    assert result.success


# ---------------------------------------------------------------------------
# Unit: register_tool implementation must not use getattr / cast
# ---------------------------------------------------------------------------


def test_register_tool_no_getattr_cast():
    src = inspect.getsource(AstraCoreClient.register_tool)
    assert "getattr" not in src, "register_tool should not use getattr"
    assert "cast(" not in src, "register_tool should not use cast()"


# ---------------------------------------------------------------------------
# Lifecycle: construction is lightweight, guard prevents premature use
# ---------------------------------------------------------------------------


def test_client_construction_is_lightweight():
    """AstraCoreClient() must not raise, even though ChromaDB / Redis are not running."""
    client = AstraCoreClient()
    assert not client._initialized
    # memory / projects facades are available immediately (they only wrap SQLite)
    assert client.memory is not None
    assert client.projects is not None


@pytest.mark.asyncio
async def test_client_chat_raises_before_aenter():
    """Calling chat() outside async-with must raise RuntimeError with a helpful message."""
    client = AstraCoreClient()
    with pytest.raises(RuntimeError, match="async context manager"):
        await client.chat("hello")


@pytest.mark.asyncio
async def test_client_register_tool_raises_before_aenter():
    """Calling register_tool() outside async-with must raise RuntimeError."""
    client = AstraCoreClient()
    with pytest.raises(RuntimeError, match="async context manager"):
        client.register_tool(name="t", func=lambda: None, description="d", parameters=[])


# ---------------------------------------------------------------------------
# ConversationOverrides TypedDict
# ---------------------------------------------------------------------------


def test_chat_options_fields():
    """ChatOptions must expose all chat control fields."""
    import dataclasses

    from astracore.modules.chat.domain.chat_options import ChatOptions

    field_names = {f.name for f in dataclasses.fields(ChatOptions)}
    expected = {
        "model_profile",
        "temperature",
        "use_tools",
        "enable_thinking",
        "thinking_budget",
        "enable_rag",
        "enable_web",
    }
    assert expected <= field_names


# ---------------------------------------------------------------------------
# Conversation async context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_context_manager_calls_clear():
    """Conversation.__aexit__ must call clear_session on the client."""
    from unittest.mock import AsyncMock

    from astracore.sdk.client import Conversation

    mock_client = AsyncMock()
    mock_client._initialized = True

    conv = Conversation(mock_client)
    sid = conv.session_id

    async with conv:
        pass

    mock_client.clear_session.assert_called_once_with(sid)
