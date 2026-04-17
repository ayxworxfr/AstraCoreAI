"""Shared test fixtures."""
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMResponse
from astracore.core.ports.memory import MemoryAdapter
from astracore.runtime.policy.engine import PolicyEngine


@pytest.fixture
def session_id():
    return uuid4()


@pytest.fixture
def user_message():
    return Message(role=MessageRole.USER, content="Hello")


@pytest.fixture
def assistant_message():
    return Message(role=MessageRole.ASSISTANT, content="Hi there")


@pytest.fixture
def mock_memory_adapter():
    adapter = AsyncMock(spec=MemoryAdapter)
    adapter.load_short_term.return_value = []
    adapter.save_short_term.return_value = None
    return adapter


@pytest.fixture
def mock_llm_response():
    return LLMResponse(content="Test response", model="claude-3-5-sonnet-20241022")


@pytest.fixture
def policy_engine():
    return PolicyEngine()
