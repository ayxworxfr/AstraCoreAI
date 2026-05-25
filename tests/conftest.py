"""Shared test fixtures."""

import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.memory.ports.memory import MemoryAdapter
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMResponse

# Keys referenced in config/config.yaml via api_key_env. Set placeholders so that
# AstraCoreConfig() can load the YAML without a real .env present.
_CONFIG_YAML_ENV_KEYS = ("ANTHROPIC_PROXY_API_KEY", "DEEPSEEK_API_KEY")


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_api_keys():
    """Inject placeholder API keys so config.yaml loads cleanly during tests.

    Real keys from .env (if present) take precedence because we use setdefault.
    """
    injected = [k for k in _CONFIG_YAML_ENV_KEYS if k not in os.environ]
    for key in injected:
        os.environ[key] = "test-key"
    yield
    for key in injected:
        os.environ.pop(key, None)


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
    return LLMResponse(content="Test response", model="claude-sonnet-4-6")


@pytest.fixture
def policy_engine():
    return PolicyEngine()
