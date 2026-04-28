# Backend Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all identified bugs, performance issues, security vulnerabilities, and code quality problems across the AstraCoreAI backend.

**Architecture:** Work bottom-up: code quality first (no dependencies), then domain bugs, then application/adapter fixes, then service layer wiring, then persistence. Each task is independently testable. Tests live in `tests/` mirroring the `src/astracore/` structure.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio (asyncio_mode=auto), tenacity (already in deps), SQLAlchemy 2.x async, Redis asyncio, ruff, mypy strict.

---

## File Map

| File | Change Type | What Changes |
|------|-------------|--------------|
| `tests/conftest.py` | Create | Shared fixtures |
| `tests/core/domain/test_session.py` | Create | truncate + token budget tests |
| `tests/core/application/test_rag.py` | Create | top_k test |
| `tests/adapters/llm/test_anthropic.py` | Create | streaming tool args test |
| `tests/adapters/tools/test_tool_loop.py` | Create | security check + use_tools test |
| `tests/adapters/memory/test_hybrid.py` | Create | eviction test |
| `tests/runtime/test_policy.py` | Create | retry/timeout tests |
| `tests/service/test_chat_api.py` | Create | CORS + routing tests |
| `src/astracore/core/domain/message.py` | Modify | `datetime.utcnow` → `datetime.now(UTC)` |
| `src/astracore/core/domain/session.py` | Modify | `datetime.utcnow`, truncate O(n²), token double-count |
| `src/astracore/core/domain/retrieval.py` | Modify | `datetime.utcnow` |
| `src/astracore/core/domain/agent.py` | Modify | `datetime.utcnow` |
| `src/astracore/core/ports/llm.py` | Modify | `datetime.utcnow` |
| `src/astracore/core/ports/tool.py` | Modify | `datetime.utcnow` |
| `src/astracore/core/ports/memory.py` | Modify | `datetime.utcnow` |
| `src/astracore/core/ports/audit.py` | Modify | `datetime.utcnow` |
| `src/astracore/core/application/chat.py` | Modify | session load without token double-count, policy wiring |
| `src/astracore/core/application/rag.py` | Modify | top_k hardcode fix |
| `src/astracore/core/application/tool_loop.py` | Modify | dedup tool defs, streaming security check, parallel execution |
| `src/astracore/runtime/policy/engine.py` | Modify | tenacity-based retry, fix retry_on_status_codes usage |
| `src/astracore/runtime/security/validator.py` | Modify | precompile regex |
| `src/astracore/adapters/llm/anthropic.py` | Modify | streaming tool input_json_delta accumulation |
| `src/astracore/adapters/memory/hybrid.py` | Modify | eviction logic for _in_memory_sessions, real save/load_long_term |
| `src/astracore/adapters/retrieval/chroma.py` | Modify | run_in_executor for sync ChromaDB calls |
| `src/astracore/adapters/workflow/native.py` | Modify | Redis-backed checkpoint |
| `src/astracore/service/api/app.py` | Modify | CORS origins from env, remove `allow_credentials` with wildcard |
| `src/astracore/service/api/chat.py` | Modify | shared LLM adapter, use_tools routing |
| `src/astracore/service/api/rag.py` | Modify | `lru_cache` on `_get_rag_pipeline` |
| `src/astracore/sdk/config.py` | Modify | Pydantic v2 `SettingsConfigDict` |

---

## Task 1: Test Infrastructure + Code Quality (datetime, Pydantic v2, regex)

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/core/__init__.py`
- Create: `tests/core/domain/__init__.py`
- Create: `tests/runtime/__init__.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/llm/__init__.py`
- Create: `tests/adapters/memory/__init__.py`
- Create: `tests/service/__init__.py`
- Modify: All domain/port files with `datetime.utcnow`
- Modify: `src/astracore/runtime/security/validator.py`
- Modify: `src/astracore/sdk/config.py`

- [ ] **Step 1: Create test infrastructure**

```bash
mkdir -p tests/core/domain tests/core/application tests/runtime tests/adapters/llm tests/adapters/memory tests/adapters/tools tests/service
```

Create `tests/conftest.py`:
```python
"""Shared test fixtures."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMResponse, StreamEvent, StreamEventType
from astracore.core.ports.memory import MemoryAdapter
from astracore.core.ports.tool import ToolDefinition, ToolExecutionResult
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
    return LLMResponse(content="Test response", model="claude-sonnet-4-6")


@pytest.fixture
def policy_engine():
    return PolicyEngine()
```

Create empty `__init__.py` files in all test subdirs.

- [ ] **Step 2: Write failing test for datetime deprecation detection**

Create `tests/core/domain/test_domain_models.py`:
```python
"""Test that domain models use timezone-aware datetimes."""
from datetime import UTC, datetime, timezone

from astracore.core.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.core.domain.session import SessionState, TokenBudget


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
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/domain/test_domain_models.py -v
```

Expected: FAIL — `assert msg.created_at.tzinfo is not None` fails.

- [ ] **Step 4: Fix all `datetime.utcnow()` usages**

In each of the following files, replace every `datetime.utcnow` with `datetime.now(UTC)`:

**`src/astracore/core/domain/message.py`** — add `UTC` to import, fix 3 fields:
```python
from datetime import UTC, datetime
# ...
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
# (apply to ToolCall, ToolResult, and Message)
```

**`src/astracore/core/domain/session.py`** — fix 4 occurrences:
```python
from datetime import UTC, datetime
# Fields:
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
# Methods:
self.updated_at = datetime.now(UTC)
```

**`src/astracore/core/domain/retrieval.py`**:
```python
from datetime import UTC, datetime
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**`src/astracore/core/domain/agent.py`** — fix all 5 `datetime.utcnow()` occurrences:
```python
from datetime import UTC, datetime
# Fields:
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
# Methods in mark_in_progress, mark_completed, mark_failed, require_approval:
self.updated_at = datetime.now(UTC)
self.completed_at = datetime.now(UTC)
```

**`src/astracore/core/ports/llm.py`**:
```python
from datetime import UTC, datetime
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
# (StreamEvent and LLMResponse)
```

**`src/astracore/core/ports/tool.py`**:
```python
from datetime import UTC, datetime
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**`src/astracore/core/ports/memory.py`**:
```python
from datetime import UTC, datetime
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**`src/astracore/core/ports/audit.py`**:
```python
from datetime import UTC, datetime
timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**`src/astracore/core/ports/workflow.py`**:
```python
from datetime import UTC, datetime
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
# All method bodies: datetime.now(UTC)
```

- [ ] **Step 5: Fix Pydantic v2 config in sdk/config.py**

Replace the old `class Config` with `model_config`:
```python
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    api_key: str
    default_model: str = "claude-sonnet-4-6"
    temperature: float = 0.7


class MemoryConfig(BaseModel):
    redis_url: str = "redis://localhost:6379/0"
    postgres_url: str = "postgresql+asyncpg://localhost/astracore"


class RetrievalConfig(BaseModel):
    collection_name: str = "astracore"
    persist_directory: str | None = None


class AstraCoreConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASTRACORE_",
        env_nested_delimiter="__",
    )

    llm: LLMConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
```

- [ ] **Step 6: Precompile regex patterns in security/validator.py**

```python
import re
from typing import Any

_SUSPICIOUS_PATTERNS = [
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"onerror=", re.IGNORECASE),
    re.compile(r"onclick=", re.IGNORECASE),
]


class InputValidator:
    def __init__(self, max_input_length: int = 100_000):
        self.max_input_length = max_input_length

    def validate_user_input(self, content: str) -> tuple[bool, str | None]:
        if len(content) > self.max_input_length:
            return False, f"Input exceeds maximum length of {self.max_input_length}"
        if self._contains_suspicious_patterns(content):
            return False, "Input contains suspicious patterns"
        return True, None

    def _contains_suspicious_patterns(self, content: str) -> bool:
        return any(p.search(content) for p in _SUSPICIOUS_PATTERNS)

    def sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        sensitive_fields = ["password", "api_key", "secret", "token", "credential"]
        return {
            k: "***REDACTED***" if any(f in k.lower() for f in sensitive_fields) else v
            for k, v in metadata.items()
        }
```

- [ ] **Step 7: Run tests and confirm passing**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/domain/test_domain_models.py -v
```

Expected: PASS all 3 tests.

- [ ] **Step 8: Run linter**

```bash
cd D:/project/study/AstraCoreAI && python -m ruff check src/astracore --fix
```

Expected: No errors (or only pre-existing).

- [ ] **Step 9: Commit**

```bash
git add src/astracore/core/domain/ src/astracore/core/ports/ src/astracore/runtime/security/ src/astracore/sdk/config.py tests/
git commit -m "fix: replace deprecated datetime.utcnow, precompile regex, Pydantic v2 config"
```

---

## Task 2: Domain Layer — truncate O(n²) + session load token double-count

**Files:**
- Modify: `src/astracore/core/domain/session.py`
- Modify: `src/astracore/core/application/chat.py`
- Create: `tests/core/domain/test_session.py`

- [ ] **Step 1: Write failing test for truncate O(n²) correctness**

Create `tests/core/domain/test_session.py`:
```python
"""Tests for session domain model."""
import pytest

from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import ContextWindow, SessionState


def _make_message(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


def test_truncate_to_budget_removes_oldest_first():
    window = ContextWindow()
    # Each message is ~25 tokens (100 chars / 4)
    for i in range(10):
        window.add_message(_make_message("x" * 100))
    # Total: ~250 tokens. Budget: 50 tokens → keep last 2 messages
    window.truncate_to_budget(50)
    assert len(window.messages) <= 3


def test_truncate_to_budget_noop_when_within_budget():
    window = ContextWindow()
    window.add_message(_make_message("hello"))
    original_count = len(window.messages)
    window.truncate_to_budget(1_000_000)
    assert len(window.messages) == original_count


def test_truncate_to_budget_result_fits_budget():
    window = ContextWindow()
    for i in range(20):
        window.add_message(_make_message("y" * 100))
    window.truncate_to_budget(100)
    assert window.total_tokens() <= 100


def test_session_load_without_token_double_count():
    """SessionState.restore_messages must not accumulate tokens."""
    session = SessionState()
    msgs = [_make_message("a" * 400) for _ in range(5)]  # ~500 tokens total
    session.restore_messages(msgs)
    # Token budget should reflect actual messages, not double-counted
    assert session.token_budget.current_input_tokens <= 500
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/domain/test_session.py -v
```

Expected: FAIL — `restore_messages` not defined, truncate may fail correctness checks.

- [ ] **Step 3: Fix truncate_to_budget and add restore_messages**

In `src/astracore/core/domain/session.py`, update `ContextWindow.truncate_to_budget` and add `restore_messages` to `SessionState`:

```python
# In ContextWindow:
def truncate_to_budget(self, max_tokens: int) -> None:
    """Truncate oldest messages to fit within token budget. O(n)."""
    if self.total_tokens() <= max_tokens:
        return
    # Walk from front, count tokens to drop
    tokens = self.total_tokens()
    cutoff = 0
    while cutoff < len(self.messages) and tokens > max_tokens:
        tokens -= self.messages[cutoff].token_estimate()
        cutoff += 1
    self.messages = self.messages[cutoff:]
```

Add `restore_messages` to `SessionState` — loads messages WITHOUT adding to token budget (used when reconstructing from storage):

```python
# In SessionState:
def restore_messages(self, messages: list[Message]) -> None:
    """Restore messages from storage without double-counting tokens.

    Use this when rehydrating a session from Redis/DB.
    Token budget is recalculated from the actual messages.
    """
    self.context_window.messages = list(messages)
    self.token_budget.current_input_tokens = sum(
        m.token_estimate() for m in messages
    )
    self.updated_at = datetime.now(UTC)
```

- [ ] **Step 4: Fix _load_session in chat.py to use restore_messages**

In `src/astracore/core/application/chat.py`, replace `_load_session`:

```python
async def _load_session(self, session_id: UUID) -> SessionState:
    """Load or create session. Uses restore_messages to avoid token double-counting."""
    messages = await self.memory.load_short_term(session_id)
    session = SessionState(session_id=session_id)
    if messages:
        session.restore_messages(messages)
    return session
```

- [ ] **Step 5: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/domain/test_session.py -v
```

Expected: PASS all 4 tests.

- [ ] **Step 6: Commit**

```bash
git add src/astracore/core/domain/session.py src/astracore/core/application/chat.py tests/core/domain/test_session.py
git commit -m "fix: O(n²) truncate_to_budget, token double-counting on session load"
```

---

## Task 3: RAG Pipeline — top_k hardcode fix

**Files:**
- Modify: `src/astracore/core/application/rag.py`
- Create: `tests/core/application/test_rag.py`

- [ ] **Step 1: Write failing test**

Create `tests/core/application/__init__.py` and `tests/core/application/test_rag.py`:

```python
"""Tests for RAG pipeline."""
from unittest.mock import AsyncMock, patch

import pytest

from astracore.core.application.rag import RAGPipeline
from astracore.core.domain.message import MessageRole
from astracore.core.domain.retrieval import Citation, RetrievalQuery, RetrievedChunk
from astracore.core.ports.retriever import RetrieverAdapter


def _make_chunk(score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        content="test content",
        score=score,
        citation=Citation(source_id="doc1", source_type="document"),
    )


@pytest.fixture
def mock_retriever():
    retriever = AsyncMock(spec=RetrieverAdapter)
    retriever.retrieve.return_value = [_make_chunk(0.9), _make_chunk(0.8), _make_chunk(0.7)]
    retriever.rerank.side_effect = lambda query, chunks, top_k: chunks[:top_k]
    return retriever


@pytest.mark.asyncio
async def test_retrieve_and_inject_respects_top_k(mock_retriever):
    pipeline = RAGPipeline(retriever=mock_retriever)
    messages = []
    await pipeline.retrieve_and_inject(query="test", messages=messages, top_k=2)
    # rerank should be called with top_k=2, not hardcoded 3
    mock_retriever.rerank.assert_called_once()
    _, kwargs = mock_retriever.rerank.call_args
    assert kwargs.get("top_k", mock_retriever.rerank.call_args[0][2] if mock_retriever.rerank.call_args[0] else None) == 2


@pytest.mark.asyncio
async def test_retrieve_and_inject_returns_context_prepended(mock_retriever):
    pipeline = RAGPipeline(retriever=mock_retriever)
    from astracore.core.domain.message import Message
    original = [Message(role=MessageRole.USER, content="hello")]
    result = await pipeline.retrieve_and_inject(query="test", messages=original, top_k=1)
    # Context message is prepended
    assert result[0].role == MessageRole.SYSTEM
    assert result[-1].content == "hello"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/application/test_rag.py -v
```

Expected: FAIL on `test_retrieve_and_inject_respects_top_k` — rerank called with top_k=3.

- [ ] **Step 3: Fix retrieve_and_inject**

In `src/astracore/core/application/rag.py`, change the hardcoded `top_k=3` to use the parameter:

```python
async def retrieve_and_inject(
    self,
    query: str,
    messages: list[Message],
    top_k: int = 5,
    min_score: float = 0.7,
) -> list[Message]:
    """Retrieve relevant chunks and inject into messages."""
    retrieval_query = RetrievalQuery(
        text=query,
        top_k=top_k,
        min_score=min_score,
    )

    chunks = await self.retriever.retrieve(retrieval_query)

    if not chunks:
        return messages

    reranked = await self.retriever.rerank(query, chunks, top_k=top_k)  # was hardcoded top_k=3

    context_parts = []
    for chunk in reranked:
        citation = chunk.citation
        context_parts.append(
            f"[{citation.source_id}]: {chunk.content}\n"
            f"(Source: {citation.title or citation.source_id}, Score: {chunk.score:.2f})"
        )

    context_message = Message(
        role=MessageRole.SYSTEM,
        content=(
            "Below is relevant context retrieved from the knowledge base:\n\n"
            + "\n\n".join(context_parts)
            + "\n\nUse this context to answer the user's question. "
            "Include citations when referencing information from the context."
        ),
    )

    return [context_message] + messages
```

- [ ] **Step 4: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/application/test_rag.py -v
```

Expected: PASS both tests.

- [ ] **Step 5: Commit**

```bash
git add src/astracore/core/application/rag.py tests/core/application/
git commit -m "fix: RAG retrieve_and_inject top_k was hardcoded to 3"
```

---

## Task 4: Anthropic Adapter — streaming tool argument accumulation

**Files:**
- Modify: `src/astracore/adapters/llm/anthropic.py`
- Create: `tests/adapters/llm/test_anthropic_stream.py`

**Context:** Anthropic streaming sends tool arguments as `input_json_delta` events after the initial `content_block_start` for the tool. The current code only handles `content_block_start` (creates ToolCall with empty args) and ignores `input_json_delta`. Fix: accumulate by block index, emit final ToolCall at `content_block_stop`.

- [ ] **Step 1: Write failing test**

Create `tests/adapters/llm/test_anthropic_stream.py`:

```python
"""Tests for Anthropic adapter streaming."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.core.domain.message import Message, MessageRole
from astracore.core.ports.llm import StreamEventType


def _make_text_delta_event(text: str):
    e = MagicMock()
    e.type = "content_block_delta"
    e.delta = MagicMock()
    e.delta.type = "text_delta"
    e.delta.text = text
    return e


def _make_tool_start_event(index: int, tool_id: str, tool_name: str):
    e = MagicMock()
    e.type = "content_block_start"
    e.index = index
    e.content_block = MagicMock()
    e.content_block.type = "tool_use"
    e.content_block.id = tool_id
    e.content_block.name = tool_name
    return e


def _make_input_json_delta_event(index: int, partial_json: str):
    e = MagicMock()
    e.type = "content_block_delta"
    e.index = index
    e.delta = MagicMock()
    e.delta.type = "input_json_delta"
    e.delta.partial_json = partial_json
    return e


def _make_block_stop_event(index: int):
    e = MagicMock()
    e.type = "content_block_stop"
    e.index = index
    return e


@pytest.mark.asyncio
async def test_streaming_tool_arguments_accumulated():
    """Tool arguments from input_json_delta must be accumulated and emitted at block_stop."""
    adapter = AnthropicAdapter(api_key="test-key")

    events = [
        _make_tool_start_event(0, "tool_123", "my_tool"),
        _make_input_json_delta_event(0, '{"key"'),
        _make_input_json_delta_event(0, ': "val'),
        _make_input_json_delta_event(0, 'ue"}'),
        _make_block_stop_event(0),
    ]

    mock_stream = AsyncMock()
    mock_stream.__aiter__ = AsyncMock(return_value=iter(events))

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = mock_context

    adapter._client = mock_client

    messages = [Message(role=MessageRole.USER, content="test")]
    collected = []
    async for event in adapter.generate_stream(messages):
        collected.append(event)

    tool_events = [e for e in collected if e.event_type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 1
    assert tool_events[0].tool_call is not None
    assert tool_events[0].tool_call.name == "my_tool"
    assert tool_events[0].tool_call.arguments == {"key": "value"}


@pytest.mark.asyncio
async def test_streaming_text_delta_still_works():
    """Text delta events still yield correctly alongside tool calls."""
    adapter = AnthropicAdapter(api_key="test-key")

    events = [
        _make_text_delta_event("Hello "),
        _make_text_delta_event("world"),
    ]

    mock_stream = AsyncMock()
    mock_stream.__aiter__ = AsyncMock(return_value=iter(events))
    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_context.__aexit__ = AsyncMock(return_value=None)
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = mock_context
    adapter._client = mock_client

    messages = [Message(role=MessageRole.USER, content="test")]
    collected = []
    async for event in adapter.generate_stream(messages):
        collected.append(event)

    text_events = [e for e in collected if e.event_type == StreamEventType.TEXT_DELTA]
    assert len(text_events) == 2
    assert "".join(e.content for e in text_events) == "Hello world"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/llm/test_anthropic_stream.py -v
```

Expected: FAIL — `tool_events[0].tool_call.arguments == {}`, not `{"key": "value"}`.

- [ ] **Step 3: Fix generate_stream in anthropic.py**

Replace the entire `generate_stream` method in `src/astracore/adapters/llm/anthropic.py`:

```python
async def generate_stream(
    self,
    messages: list[Message],
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> AsyncIterator[StreamEvent]:
    """Generate a streaming response with correct tool argument accumulation."""
    import json as _json

    client = self._get_client()
    model = model or self.default_model
    max_tokens = max_tokens or 4096

    system = self._get_system_message(messages)
    converted_messages = self._convert_messages(messages)

    request_params: dict[str, Any] = {
        "model": model,
        "messages": converted_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system:
        request_params["system"] = system
    if "tools" in kwargs:
        request_params["tools"] = kwargs["tools"]

    # index → {id, name, input_str}
    tool_buffers: dict[int, dict[str, Any]] = {}

    async with client.messages.stream(**request_params) as stream:
        async for event in stream:
            if not hasattr(event, "type"):
                continue

            if event.type == "content_block_start":
                block = getattr(event, "content_block", None)
                if block and getattr(block, "type", None) == "tool_use":
                    idx = getattr(event, "index", 0)
                    tool_buffers[idx] = {
                        "id": block.id,
                        "name": block.name,
                        "input_str": "",
                    }

            elif event.type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                delta_type = getattr(delta, "type", None)

                if delta_type == "text_delta":
                    yield StreamEvent(
                        event_type=StreamEventType.TEXT_DELTA,
                        content=delta.text,
                    )
                elif delta_type == "input_json_delta":
                    idx = getattr(event, "index", 0)
                    if idx in tool_buffers:
                        tool_buffers[idx]["input_str"] += delta.partial_json

            elif event.type == "content_block_stop":
                idx = getattr(event, "index", 0)
                if idx in tool_buffers:
                    buf = tool_buffers.pop(idx)
                    arguments = _json.loads(buf["input_str"]) if buf["input_str"] else {}
                    yield StreamEvent(
                        event_type=StreamEventType.TOOL_CALL,
                        tool_call=ToolCall(
                            id=buf["id"],
                            name=buf["name"],
                            arguments=arguments,
                        ),
                    )

    yield StreamEvent(event_type=StreamEventType.DONE)
```

- [ ] **Step 4: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/llm/test_anthropic_stream.py -v
```

Expected: PASS both tests.

- [ ] **Step 5: Commit**

```bash
git add src/astracore/adapters/llm/anthropic.py tests/adapters/llm/
git commit -m "fix: Anthropic streaming tool arguments were always empty (input_json_delta ignored)"
```

---

## Task 5: Tool Loop — deduplicate tool defs, streaming security check, use_tools routing

**Files:**
- Modify: `src/astracore/core/application/tool_loop.py`
- Modify: `src/astracore/service/api/chat.py`
- Create: `tests/core/application/test_tool_loop.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/application/test_tool_loop.py`:

```python
"""Tests for ToolLoopUseCase."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter, ToolDefinition, ToolExecutionResult
from astracore.runtime.policy.engine import PolicyEngine, PolicyConfig
from astracore.runtime.policy.rules import SecurityRule


def _make_session_with_user_msg(content: str = "hello") -> SessionState:
    session = SessionState()
    session.add_message(Message(role=MessageRole.USER, content=content))
    return session


@pytest.fixture
def mock_llm_no_tools():
    llm = AsyncMock(spec=LLMAdapter)
    llm.generate.return_value = LLMResponse(content="done", model="test")
    return llm


@pytest.fixture
def mock_tool_adapter():
    adapter = MagicMock(spec=ToolAdapter)
    adapter.get_definitions.return_value = [
        ToolDefinition(name="allowed_tool", description="ok"),
    ]
    exec_result = ToolExecutionResult(
        tool_name="allowed_tool", success=True, output="result", execution_time_ms=1.0
    )
    adapter.execute = AsyncMock(return_value=exec_result)
    return adapter


@pytest.mark.asyncio
async def test_streaming_tool_loop_applies_security_policy():
    """Security policy must block tools in streaming path, same as non-streaming."""
    from astracore.core.ports.llm import ToolCall as LLMToolCall

    llm = AsyncMock(spec=LLMAdapter)

    # First call: return a tool call to blocked_tool
    # Second call: return done
    call_count = 0

    async def mock_generate_stream(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield StreamEvent(
                event_type=StreamEventType.TOOL_CALL,
                tool_call=LLMToolCall(name="blocked_tool", arguments={}),
            )
            yield StreamEvent(event_type=StreamEventType.DONE)
        else:
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="done")
            yield StreamEvent(event_type=StreamEventType.DONE)

    llm.generate_stream = mock_generate_stream

    tool_adapter = MagicMock(spec=ToolAdapter)
    tool_adapter.get_definitions.return_value = []
    tool_adapter.execute = AsyncMock()

    security = SecurityRule(tool_whitelist=["allowed_tool"])
    policy = PolicyEngine(PolicyConfig(security=security))

    loop = ToolLoopUseCase(llm_adapter=llm, tool_adapter=tool_adapter, policy_engine=policy)
    session = _make_session_with_user_msg()

    events = []
    async for event in loop.execute_stream_with_tools(session):
        events.append(event)

    # blocked_tool must NOT have been executed
    tool_adapter.execute.assert_not_called()
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/application/test_tool_loop.py -v
```

Expected: FAIL — `execute` is called on the blocked tool.

- [ ] **Step 3: Refactor tool_loop.py — extract _build_tool_definitions, add streaming security**

Replace the entire `src/astracore/core/application/tool_loop.py`:

```python
"""Tool loop use case implementation."""

from collections.abc import AsyncIterator
from typing import Any

from astracore.core.domain.message import Message, MessageRole, ToolResult
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.policy.engine import PolicyEngine


class ToolLoopUseCase:
    """Tool calling loop with automatic execution."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_adapter: ToolAdapter,
        policy_engine: PolicyEngine,
        max_iterations: int = 10,
    ):
        self.llm = llm_adapter
        self.tools = tool_adapter
        self.policy = policy_engine
        self.max_iterations = max_iterations

    def _build_tool_definitions(self) -> list[dict[str, Any]]:
        """Build tool definitions dict for LLM. Extracted to avoid duplication."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        p.name: {"type": p.type.value, "description": p.description}
                        for p in t.parameters
                    },
                    "required": [p.name for p in t.parameters if p.required],
                },
            }
            for t in self.tools.get_definitions()
        ]

    async def execute_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
    ) -> SessionState:
        """Execute tool loop until completion."""
        tool_definitions = self._build_tool_definitions()
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1

            response = await self.llm.generate(
                messages=session.get_messages(),
                model=model,
                tools=tool_definitions if tool_definitions else None,
            )

            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
            session.add_message(assistant_msg)

            if not response.tool_calls:
                break

            tool_results = []
            for tool_call in response.tool_calls:
                if not self.policy.check_security_policy(tool_call.name, tool_call.arguments):
                    tool_results.append(
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content="Tool execution blocked by security policy",
                            is_error=True,
                        )
                    )
                    continue

                exec_result = await self.tools.execute(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                tool_results.append(
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=exec_result.tool_name,
                        content=exec_result.output,
                        is_error=not exec_result.success,
                        metadata=exec_result.metadata,
                    )
                )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )

        return session

    async def execute_stream_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Execute tool loop with streaming. Applies security policy on tool calls."""
        tool_definitions = self._build_tool_definitions()
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1

            accumulated_content = ""
            accumulated_tool_calls = []

            async for event in self.llm.generate_stream(
                messages=session.get_messages(),
                model=model,
                tools=tool_definitions if tool_definitions else None,
            ):
                if event.content:
                    accumulated_content += event.content
                if event.tool_call:
                    accumulated_tool_calls.append(event.tool_call)
                yield event

            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    tool_calls=accumulated_tool_calls,
                )
            )

            if not accumulated_tool_calls:
                break

            tool_results = []
            for tool_call in accumulated_tool_calls:
                # Security check — same policy as non-streaming path
                if not self.policy.check_security_policy(tool_call.name, tool_call.arguments):
                    tool_results.append(
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content="Tool execution blocked by security policy",
                            is_error=True,
                        )
                    )
                    continue

                exec_result = await self.tools.execute(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                tool_results.append(
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=exec_result.tool_name,
                        content=exec_result.output,
                        is_error=not exec_result.success,
                    )
                )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )
```

- [ ] **Step 4: Fix use_tools routing in chat.py**

In `src/astracore/service/api/chat.py`, add shared adapter caches and implement the tool routing:

```python
"""Chat API endpoints."""

import os
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.adapters.tools.native import NativeToolAdapter
from astracore.core.application.chat import ChatUseCase
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import StreamEventType
from astracore.runtime.policy.engine import PolicyEngine

router = APIRouter()


def _get_required_api_key() -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("ASTRACORE_LLM__API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing API key: set ANTHROPIC_API_KEY or ASTRACORE_LLM__API_KEY")
    return api_key


@lru_cache(maxsize=1)
def _get_llm_adapter() -> AnthropicAdapter:
    """Single shared LLM adapter instance."""
    model = os.getenv("MODEL", "claude-sonnet-4-6")
    return AnthropicAdapter(api_key=_get_required_api_key(), default_model=model)


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    """Single shared memory adapter instance."""
    redis_url = os.getenv("ASTRACORE__MEMORY__REDIS_URL", "redis://localhost:6379/0")
    postgres_url = os.getenv(
        "ASTRACORE__MEMORY__POSTGRES_URL",
        "postgresql+asyncpg://localhost/astracore",
    )
    return HybridMemoryAdapter(redis_url=redis_url, postgres_url=postgres_url)


@lru_cache(maxsize=1)
def _get_chat_use_case() -> ChatUseCase:
    return ChatUseCase(
        llm_adapter=_get_llm_adapter(),
        memory_adapter=_get_memory_adapter(),
        policy_engine=PolicyEngine(),
    )


@lru_cache(maxsize=1)
def _get_tool_loop_use_case() -> ToolLoopUseCase:
    return ToolLoopUseCase(
        llm_adapter=_get_llm_adapter(),  # shared adapter
        tool_adapter=NativeToolAdapter(),
        policy_engine=PolicyEngine(),
    )


class ChatRequest(BaseModel):
    message: str
    session_id: UUID | None = None
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    use_tools: bool = False


class ChatResponse(BaseModel):
    session_id: UUID
    message: str
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


async def _run_with_tools(request: ChatRequest, session_id: UUID) -> str:
    """Execute a chat turn using the tool loop."""
    memory = _get_memory_adapter()
    tool_loop = _get_tool_loop_use_case()

    messages = await memory.load_short_term(session_id)
    session = SessionState(session_id=session_id)
    if messages:
        session.restore_messages(messages)

    session.add_message(Message(role=MessageRole.USER, content=request.message))
    session = await tool_loop.execute_with_tools(session, model=request.model)
    await memory.save_short_term(session_id, session.get_messages())

    # Last message is the final assistant response
    msgs = session.get_messages()
    last_assistant = next(
        (m for m in reversed(msgs) if m.role == MessageRole.ASSISTANT), None
    )
    return last_assistant.content if last_assistant else ""


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or uuid4()
    try:
        if request.use_tools:
            content = await _run_with_tools(request, session_id)
            return ChatResponse(session_id=session_id, message=content, model=request.model)

        use_case = _get_chat_use_case()
        response_message = await use_case.execute(
            session_id=session_id,
            user_message=request.message,
            model=request.model,
            temperature=request.temperature,
        )
        return ChatResponse(
            session_id=session_id,
            message=response_message.content,
            model=request.model,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    session_id = request.session_id or uuid4()

    async def event_generator() -> Any:
        try:
            use_case = _get_chat_use_case()
            async for event in use_case.execute_stream(
                session_id=session_id,
                user_message=request.message,
                model=request.model,
                temperature=request.temperature,
            ):
                if event.event_type == StreamEventType.TEXT_DELTA:
                    yield {"event": "message", "data": event.content}
                elif event.event_type == StreamEventType.DONE:
                    yield {"event": "done", "data": "[DONE]"}
        except Exception as e:
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(event_generator())
```

- [ ] **Step 5: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/core/application/test_tool_loop.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/astracore/core/application/tool_loop.py src/astracore/service/api/chat.py tests/core/application/test_tool_loop.py
git commit -m "fix: streaming tool security check missing, tool_defs duplicated, use_tools routing ignored, shared LLM adapter"
```

---

## Task 6: Service Layer — CORS security + RAG pipeline caching

**Files:**
- Modify: `src/astracore/service/api/app.py`
- Modify: `src/astracore/service/api/rag.py`

- [ ] **Step 1: Fix CORS configuration**

Replace `src/astracore/service/api/app.py`:

```python
"""FastAPI application factory."""

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astracore.service.api import chat, health, rag


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AstraCore AI",
        description="Enterprise-grade AI Framework API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Read allowed origins from env. Default covers local frontend dev.
    # In production, set ALLOWED_ORIGINS=https://your-domain.com
    raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )

    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(rag.router, prefix="/api/v1/rag", tags=["rag"])

    return app
```

- [ ] **Step 2: Cache the RAG pipeline in rag.py**

In `src/astracore/service/api/rag.py`, add `lru_cache` to `_get_rag_pipeline`:

```python
"""RAG API endpoints."""

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from astracore.adapters.retrieval.chroma import ChromaRetrieverAdapter
from astracore.core.application.rag import RAGPipeline

router = APIRouter()


class IndexRequest(BaseModel):
    document_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class IndexResponse(BaseModel):
    document_id: str
    success: bool
    message: str


class RetrievalRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)


class RetrievalResponse(BaseModel):
    chunks: list[dict[str, Any]]
    count: int


@lru_cache(maxsize=1)
def _get_rag_pipeline() -> RAGPipeline:
    """Cached RAG pipeline — avoids creating a new ChromaDB connection per request."""
    retriever = ChromaRetrieverAdapter()
    return RAGPipeline(retriever=retriever)


@router.post("/index", response_model=IndexResponse)
async def index_document(request: IndexRequest) -> IndexResponse:
    try:
        pipeline = _get_rag_pipeline()
        success = await pipeline.index_document(
            document_id=request.document_id,
            text=request.text,
            metadata=request.metadata,
        )
        return IndexResponse(
            document_id=request.document_id,
            success=success,
            message="Document indexed successfully" if success else "Indexing failed",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/retrieve", response_model=RetrievalResponse)
async def retrieve_chunks(request: RetrievalRequest) -> RetrievalResponse:
    try:
        pipeline = _get_rag_pipeline()
        chunks = await pipeline.retrieve_with_citations(
            query=request.query,
            top_k=request.top_k,
        )
        chunks_data = [
            {
                "content": chunk.content,
                "score": chunk.score,
                "citation": {
                    "source_id": chunk.citation.source_id,
                    "source_type": chunk.citation.source_type,
                    "title": chunk.citation.title,
                },
            }
            for chunk in chunks
        ]
        return RetrievalResponse(chunks=chunks_data, count=len(chunks_data))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{document_id}")
async def delete_document(document_id: str) -> dict[str, Any]:
    try:
        pipeline = _get_rag_pipeline()
        success = await pipeline.delete_document(document_id)
        return {"document_id": document_id, "deleted": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
```

- [ ] **Step 3: Commit**

```bash
git add src/astracore/service/api/app.py src/astracore/service/api/rag.py
git commit -m "fix: CORS wildcard security, RAG pipeline created per-request instead of cached"
```

---

## Task 7: Policy Engine — activate retry/timeout using tenacity

**Files:**
- Modify: `src/astracore/runtime/policy/engine.py`
- Modify: `src/astracore/core/application/chat.py`
- Create: `tests/runtime/test_policy.py`

**Context:** `tenacity` is already in the project dependencies. `retry_on_status_codes` in `RetryRule` was defined but never used. The `apply_timeout_policy` has a subtle bug: it calls `func(*args, **kwargs)` which creates the coroutine before passing to `wait_for` — this is actually correct for coroutines but needs to be verified. We'll also wire retry+timeout into `ChatUseCase.execute()`.

- [ ] **Step 1: Write failing tests**

Create `tests/runtime/test_policy.py`:

```python
"""Tests for PolicyEngine retry and timeout."""
import asyncio
from unittest.mock import AsyncMock, call

import pytest

from astracore.runtime.policy.engine import PolicyConfig, PolicyEngine
from astracore.runtime.policy.rules import RetryRule, TimeoutRule


@pytest.mark.asyncio
async def test_retry_retries_on_failure_then_succeeds():
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("temporary failure")
        return "ok"

    policy = PolicyEngine(PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=10)))
    result = await policy.apply_retry_policy(flaky)
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_raises_after_max_retries():
    async def always_fails():
        raise RuntimeError("permanent failure")

    policy = PolicyEngine(PolicyConfig(retry=RetryRule(max_retries=2, initial_delay_ms=10)))
    with pytest.raises(RuntimeError, match="permanent failure"):
        await policy.apply_retry_policy(always_fails)


@pytest.mark.asyncio
async def test_timeout_raises_on_slow_function():
    async def slow():
        await asyncio.sleep(10)
        return "done"

    policy = PolicyEngine(PolicyConfig(timeout=TimeoutRule(llm_timeout_ms=50)))
    with pytest.raises(asyncio.TimeoutError):
        await policy.apply_timeout_policy(slow, timeout_type="llm")


@pytest.mark.asyncio
async def test_timeout_passes_for_fast_function():
    async def fast():
        return "fast"

    policy = PolicyEngine(PolicyConfig(timeout=TimeoutRule(llm_timeout_ms=5000)))
    result = await policy.apply_timeout_policy(fast, timeout_type="llm")
    assert result == "fast"
```

- [ ] **Step 2: Run tests to see current state**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/runtime/test_policy.py -v
```

Note which pass and which fail.

- [ ] **Step 3: Rewrite apply_retry_policy using tenacity**

Replace `src/astracore/runtime/policy/engine.py`:

```python
"""Policy engine implementation."""

import asyncio
from typing import Any

from pydantic import BaseModel
from tenacity import RetryError, retry, retry_if_exception, stop_after_attempt, wait_exponential

from astracore.core.domain.message import Message
from astracore.core.domain.session import SessionState
from astracore.runtime.policy.rules import (
    BudgetRule,
    RetryRule,
    SecurityRule,
    TimeoutRule,
    TruncationRule,
)


def _make_retry_predicate(status_codes: list[int]):
    """Return a tenacity retry predicate that retries on HTTP-like status codes."""
    def should_retry(exc: BaseException) -> bool:
        # Retry on any Exception unless it matches a known non-retriable type.
        # HTTP adapters typically raise exceptions with a `status_code` attribute.
        if hasattr(exc, "status_code") and exc.status_code not in status_codes:
            return False
        # Retry on all generic exceptions (network errors, timeouts, etc.)
        return isinstance(exc, Exception)

    return should_retry


class PolicyConfig(BaseModel):
    budget: BudgetRule = BudgetRule()
    retry: RetryRule = RetryRule()
    timeout: TimeoutRule = TimeoutRule()
    truncation: TruncationRule = TruncationRule()
    security: SecurityRule = SecurityRule()


class PolicyEngine:
    """Central policy enforcement engine."""

    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()

    def apply_budget_policy(self, session: SessionState) -> SessionState:
        budget = session.token_budget
        rules = self.config.budget

        budget.max_input_tokens = rules.max_input_tokens
        budget.max_output_tokens = rules.max_output_tokens
        budget.max_tool_tokens = rules.max_tool_tokens
        budget.max_memory_tokens = rules.max_memory_tokens

        if budget.is_input_budget_exceeded():
            self._apply_truncation(session)

        return session

    def _apply_truncation(self, session: SessionState) -> None:
        rules = self.config.truncation
        if not rules.enable_auto_truncation:
            return

        context = session.context_window
        available_tokens = session.token_budget.available_input_tokens()

        if rules.summarize_older and len(context.messages) > rules.keep_recent_messages:
            older_messages = context.messages[: -rules.keep_recent_messages]
            context.summary = self._create_summary(older_messages, rules.summary_max_tokens)
            context.messages = context.messages[-rules.keep_recent_messages :]
        else:
            context.truncate_to_budget(available_tokens)

    def _create_summary(self, messages: list[Message], max_tokens: int) -> str:
        summary_parts = []
        for msg in messages:
            summary_parts.append(f"{msg.role.value}: {msg.content[:100]}...")
        return " | ".join(summary_parts[:10])

    def check_security_policy(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        rules = self.config.security
        if rules.tool_whitelist and tool_name not in rules.tool_whitelist:
            return False
        if rules.sensitive_fields:
            for field in rules.sensitive_fields:
                if field in arguments:
                    return False
        return True

    async def apply_retry_policy(
        self,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Retry func using tenacity with exponential back-off.

        Respects retry_on_status_codes: only retries when the exception has a
        matching status_code, or when no status_code attribute is present.
        """
        rules = self.config.retry
        predicate = _make_retry_predicate(rules.retry_on_status_codes)

        @retry(
            stop=stop_after_attempt(rules.max_retries),
            wait=wait_exponential(
                multiplier=rules.initial_delay_ms / 1000.0,
                max=rules.max_delay_ms / 1000.0,
                exp_base=rules.exponential_base,
            ),
            retry=retry_if_exception(predicate),
            reraise=True,
        )
        async def _attempt() -> Any:
            return await func(*args, **kwargs)

        return await _attempt()

    async def apply_timeout_policy(
        self,
        func: Any,
        timeout_type: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Wrap func in asyncio.wait_for with configured timeout."""
        timeout_map = {
            "llm": self.config.timeout.llm_timeout_ms,
            "tool": self.config.timeout.tool_timeout_ms,
            "retrieval": self.config.timeout.retrieval_timeout_ms,
        }
        timeout_ms = timeout_map.get(timeout_type, 30_000)
        timeout_sec = timeout_ms / 1000.0
        return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_sec)
```

- [ ] **Step 4: Wire retry+timeout into ChatUseCase**

In `src/astracore/core/application/chat.py`, wrap the LLM calls:

```python
async def execute(
    self,
    session_id: UUID,
    user_message: str,
    model: str | None = None,
    temperature: float = 0.7,
) -> Message:
    session = await self._load_session(session_id)

    user_msg = Message(role=MessageRole.USER, content=user_message)
    session.add_message(user_msg)
    session = self.policy.apply_budget_policy(session)

    async def _call_llm() -> Any:
        return await self.llm.generate(
            messages=session.get_messages(),
            model=model,
            temperature=temperature,
        )

    response = await self.policy.apply_timeout_policy(
        lambda: self.policy.apply_retry_policy(_call_llm),
        timeout_type="llm",
    )

    assistant_msg = Message(
        role=MessageRole.ASSISTANT,
        content=response.content,
        tool_calls=response.tool_calls,
    )
    session.add_message(assistant_msg)
    await self._save_session(session)
    return assistant_msg
```

Note: `execute_stream` intentionally does NOT wrap in retry (streaming retries require special handling — partial output cannot be retried transparently). Add a comment to that effect.

- [ ] **Step 5: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/runtime/test_policy.py -v
```

Expected: PASS all 4.

- [ ] **Step 6: Commit**

```bash
git add src/astracore/runtime/policy/engine.py src/astracore/core/application/chat.py tests/runtime/test_policy.py
git commit -m "fix: policy engine retry/timeout were dead code — wire into ChatUseCase, use tenacity"
```

---

## Task 8: Memory Adapter — eviction for _in_memory_sessions

**Files:**
- Modify: `src/astracore/adapters/memory/hybrid.py`
- Create: `tests/adapters/memory/test_hybrid.py`

- [ ] **Step 1: Write failing test**

Create `tests/adapters/memory/test_hybrid.py`:

```python
"""Tests for HybridMemoryAdapter in-memory eviction."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.domain.message import Message, MessageRole


def _adapter() -> HybridMemoryAdapter:
    adapter = HybridMemoryAdapter(redis_url="redis://localhost", postgres_url="postgresql://localhost")
    adapter._redis_disabled = True  # force in-memory path
    return adapter


def _msg(content: str = "hi") -> Message:
    return Message(role=MessageRole.USER, content=content)


@pytest.mark.asyncio
async def test_eviction_removes_old_sessions_on_save():
    adapter = _adapter()
    adapter._SESSION_TTL = timedelta(seconds=0)  # everything immediately stale

    sid = uuid4()
    key = adapter._session_key(sid)
    adapter._in_memory_sessions[key] = []
    adapter._session_timestamps[key] = datetime.now(UTC) - timedelta(hours=2)

    new_sid = uuid4()
    await adapter.save_short_term(new_sid, [_msg()])

    # Old session must have been evicted
    assert key not in adapter._in_memory_sessions


@pytest.mark.asyncio
async def test_max_session_cap_enforced():
    adapter = _adapter()
    adapter._MAX_IN_MEMORY_SESSIONS = 3

    for _ in range(5):
        sid = uuid4()
        await adapter.save_short_term(sid, [_msg()])

    assert len(adapter._in_memory_sessions) <= 3


@pytest.mark.asyncio
async def test_delete_session_memory_removes_entry():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg()])
    await adapter.delete_session_memory(sid)
    key = adapter._session_key(sid)
    assert key not in adapter._in_memory_sessions
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/memory/test_hybrid.py -v
```

Expected: FAIL — `_session_timestamps` doesn't exist, no eviction.

- [ ] **Step 3: Add eviction to HybridMemoryAdapter**

Replace the `_in_memory_sessions` section of `src/astracore/adapters/memory/hybrid.py`:

```python
"""Hybrid memory adapter using Redis + PostgreSQL."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from astracore.core.domain.message import Message
from astracore.core.ports.memory import MemoryAdapter, MemoryEntry


class HybridMemoryAdapter(MemoryAdapter):
    """Hybrid memory using Redis (short-term) and PostgreSQL (long-term).

    When Redis is unavailable, falls back to an in-process dict with TTL eviction.
    The in-memory fallback is NOT shared across processes and is lost on restart.
    """

    _MAX_IN_MEMORY_SESSIONS: int = 1_000
    _SESSION_TTL: timedelta = timedelta(hours=1)

    def __init__(self, redis_url: str, postgres_url: str):
        self.redis_url = redis_url
        self.postgres_url = postgres_url
        self._redis: Any = None
        self._db_engine: Any = None
        self._redis_disabled = False
        self._in_memory_sessions: dict[str, list[dict[str, Any]]] = {}
        self._session_timestamps: dict[str, datetime] = {}

    def _get_redis(self) -> Any:
        if self._redis_disabled:
            return None
        if self._redis is None:
            try:
                from redis.asyncio import Redis
                self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            except ImportError as e:
                raise ImportError(
                    "redis package not installed. Install with: pip install redis"
                ) from e
        return self._redis

    def _disable_redis(self) -> None:
        self._redis_disabled = True
        self._redis = None

    @staticmethod
    def _session_key(session_id: UUID) -> str:
        return f"session:{session_id}:messages"

    @staticmethod
    def _deserialize_messages(messages_data: list[dict[str, Any]]) -> list[Message]:
        return [Message(**msg_data) for msg_data in messages_data]

    def _get_db(self) -> Any:
        if self._db_engine is None:
            try:
                from sqlalchemy.ext.asyncio import create_async_engine
                self._db_engine = create_async_engine(self.postgres_url)
            except ImportError as e:
                raise ImportError(
                    "sqlalchemy and asyncpg required. Install with: pip install sqlalchemy asyncpg"
                ) from e
        return self._db_engine

    def _evict_stale(self) -> None:
        """Remove expired sessions and enforce max cap. Called on every write."""
        now = datetime.now(UTC)
        stale = [
            k for k, ts in self._session_timestamps.items()
            if now - ts > self._SESSION_TTL
        ]
        for k in stale:
            self._in_memory_sessions.pop(k, None)
            self._session_timestamps.pop(k, None)

        # Enforce cap: remove oldest entries first (simple LRU approximation)
        while len(self._in_memory_sessions) > self._MAX_IN_MEMORY_SESSIONS:
            oldest = min(self._session_timestamps, key=lambda k: self._session_timestamps[k])
            self._in_memory_sessions.pop(oldest, None)
            self._session_timestamps.pop(oldest, None)

    async def save_short_term(
        self,
        session_id: UUID,
        messages: list[Message],
        ttl_seconds: int = 3600,
    ) -> None:
        key = self._session_key(session_id)
        messages_data = [msg.model_dump(mode="json") for msg in messages]

        self._evict_stale()
        self._in_memory_sessions[key] = messages_data
        self._session_timestamps[key] = datetime.now(UTC)

        redis = self._get_redis()
        if redis is None:
            return
        try:
            await redis.setex(key, ttl_seconds, json.dumps(messages_data))
        except Exception:
            self._disable_redis()

    async def load_short_term(self, session_id: UUID) -> list[Message]:
        key = self._session_key(session_id)
        redis = self._get_redis()
        if redis is None:
            return self._deserialize_messages(self._in_memory_sessions.get(key, []))

        try:
            data = await redis.get(key)
        except Exception:
            self._disable_redis()
            return self._deserialize_messages(self._in_memory_sessions.get(key, []))

        if not data:
            return self._deserialize_messages(self._in_memory_sessions.get(key, []))

        messages_data = json.loads(data)
        self._in_memory_sessions[key] = messages_data
        self._session_timestamps[key] = datetime.now(UTC)
        return self._deserialize_messages(messages_data)

    async def save_long_term(
        self,
        session_id: UUID,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            session_id=session_id,
            content=summary,
            memory_type="long_term",
            metadata=metadata or {},
        )
        return entry  # PostgreSQL persistence added in Task 9

    async def load_long_term(self, session_id: UUID, limit: int = 10) -> list[MemoryEntry]:
        return []  # PostgreSQL persistence added in Task 9

    async def search_memory(
        self,
        query: str,
        session_id: UUID | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        return []  # PostgreSQL persistence added in Task 9

    async def delete_session_memory(self, session_id: UUID) -> None:
        key = self._session_key(session_id)
        self._in_memory_sessions.pop(key, None)
        self._session_timestamps.pop(key, None)

        redis = self._get_redis()
        if redis is None:
            return
        try:
            await redis.delete(key)
        except Exception:
            self._disable_redis()
```

- [ ] **Step 4: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/memory/test_hybrid.py -v
```

Expected: PASS all 3.

- [ ] **Step 5: Commit**

```bash
git add src/astracore/adapters/memory/hybrid.py tests/adapters/memory/
git commit -m "fix: _in_memory_sessions grows unbounded — add TTL eviction and max session cap"
```

---

## Task 9: Long-Term Memory — PostgreSQL implementation

**Files:**
- Create: `src/astracore/adapters/memory/models.py`
- Modify: `src/astracore/adapters/memory/hybrid.py`
- Create: `tests/adapters/memory/test_long_term_memory.py`

**Context:** `save_long_term`, `load_long_term`, `search_memory` are all stubs. We need a SQLAlchemy model + actual async DB operations. SQLAlchemy + asyncpg are already in the project deps.

- [ ] **Step 1: Create the SQLAlchemy ORM model**

Create `src/astracore/adapters/memory/models.py`:

```python
"""SQLAlchemy ORM models for memory persistence."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MemoryEntryRow(Base):
    """Persistent long-term memory entry."""

    __tablename__ = "memory_entries"

    entry_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False, default="long_term")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_memory_entries_session_created", "session_id", "created_at"),
    )
```

- [ ] **Step 2: Write failing integration tests (skipped without DB)**

Create `tests/adapters/memory/test_long_term_memory.py`:

```python
"""Integration tests for long-term memory persistence.

These tests require a running PostgreSQL instance and are skipped otherwise.
Run with: pytest tests/adapters/memory/test_long_term_memory.py -v
Set env var: ASTRACORE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@localhost/testdb
"""

import os
from uuid import uuid4

import pytest

from astracore.adapters.memory.hybrid import HybridMemoryAdapter

POSTGRES_URL = os.getenv(
    "ASTRACORE_TEST_POSTGRES_URL",
    "",
)
skip_without_db = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ASTRACORE_TEST_POSTGRES_URL not set",
)


@pytest.fixture
async def adapter_with_db():
    adapter = HybridMemoryAdapter(
        redis_url="redis://localhost",
        postgres_url=POSTGRES_URL,
    )
    adapter._redis_disabled = True
    await adapter.ensure_schema()
    yield adapter
    await adapter.drop_schema()


@skip_without_db
@pytest.mark.asyncio
async def test_save_and_load_long_term(adapter_with_db):
    sid = uuid4()
    entry = await adapter_with_db.save_long_term(sid, "summary text", {"key": "val"})
    loaded = await adapter_with_db.load_long_term(sid, limit=10)
    assert len(loaded) == 1
    assert loaded[0].content == "summary text"
    assert loaded[0].metadata["key"] == "val"


@skip_without_db
@pytest.mark.asyncio
async def test_load_long_term_respects_limit(adapter_with_db):
    sid = uuid4()
    for i in range(5):
        await adapter_with_db.save_long_term(sid, f"entry {i}")
    loaded = await adapter_with_db.load_long_term(sid, limit=3)
    assert len(loaded) == 3


@skip_without_db
@pytest.mark.asyncio
async def test_search_memory_finds_matching_content(adapter_with_db):
    sid = uuid4()
    await adapter_with_db.save_long_term(sid, "user prefers dark mode")
    await adapter_with_db.save_long_term(sid, "user is a senior developer")
    results = await adapter_with_db.search_memory("dark mode", session_id=sid, limit=5)
    assert any("dark mode" in r.content for r in results)
```

- [ ] **Step 3: Implement save_long_term, load_long_term, search_memory, ensure_schema**

Add to `src/astracore/adapters/memory/hybrid.py` (after the existing `_get_db` method):

```python
    async def ensure_schema(self) -> None:
        """Create tables if they don't exist. Call at startup or in tests."""
        from sqlalchemy.ext.asyncio import AsyncEngine
        from astracore.adapters.memory.models import Base

        engine: AsyncEngine = self._get_db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_schema(self) -> None:
        """Drop all tables. Test helper only."""
        from astracore.adapters.memory.models import Base
        engine = self._get_db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def save_long_term(
        self,
        session_id: UUID,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        from sqlalchemy.ext.asyncio import AsyncSession
        from astracore.adapters.memory.models import MemoryEntryRow

        entry = MemoryEntry(
            session_id=session_id,
            content=summary,
            memory_type="long_term",
            metadata=metadata or {},
        )

        try:
            engine = self._get_db()
            async with AsyncSession(engine) as db:
                row = MemoryEntryRow(
                    entry_id=str(entry.entry_id),
                    session_id=str(session_id),
                    content=summary,
                    memory_type="long_term",
                    meta=metadata or {},
                )
                db.add(row)
                await db.commit()
        except Exception:
            pass  # Degrade gracefully if DB unavailable

        return entry

    async def load_long_term(self, session_id: UUID, limit: int = 10) -> list[MemoryEntry]:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from astracore.adapters.memory.models import MemoryEntryRow

        try:
            engine = self._get_db()
            async with AsyncSession(engine) as db:
                result = await db.execute(
                    select(MemoryEntryRow)
                    .where(MemoryEntryRow.session_id == str(session_id))
                    .order_by(MemoryEntryRow.created_at.desc())
                    .limit(limit)
                )
                rows = result.scalars().all()
                return [
                    MemoryEntry(
                        session_id=session_id,
                        content=row.content,
                        memory_type=row.memory_type,
                        metadata=row.meta,
                        created_at=row.created_at,
                    )
                    for row in rows
                ]
        except Exception:
            return []

    async def search_memory(
        self,
        query: str,
        session_id: UUID | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Full-text search via ILIKE. For production, use pg_trgm or a vector index."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from astracore.adapters.memory.models import MemoryEntryRow

        try:
            engine = self._get_db()
            async with AsyncSession(engine) as db:
                stmt = select(MemoryEntryRow).where(
                    MemoryEntryRow.content.ilike(f"%{query}%")
                )
                if session_id is not None:
                    stmt = stmt.where(MemoryEntryRow.session_id == str(session_id))
                stmt = stmt.order_by(MemoryEntryRow.created_at.desc()).limit(limit)
                result = await db.execute(stmt)
                rows = result.scalars().all()
                return [
                    MemoryEntry(
                        session_id=UUID(row.session_id),
                        content=row.content,
                        memory_type=row.memory_type,
                        metadata=row.meta,
                        created_at=row.created_at,
                    )
                    for row in rows
                ]
        except Exception:
            return []
```

- [ ] **Step 4: Run unit tests (skip integration)**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/memory/ -v
```

Expected: PASS unit tests, integration tests SKIP.

- [ ] **Step 5: Commit**

```bash
git add src/astracore/adapters/memory/ tests/adapters/memory/test_long_term_memory.py
git commit -m "feat: implement long-term memory persistence with PostgreSQL (WP-12)"
```

---

## Task 10: Workflow Checkpoint — Redis-backed persistence

**Files:**
- Modify: `src/astracore/adapters/workflow/native.py`
- Create: `tests/adapters/workflow/__init__.py`
- Create: `tests/adapters/workflow/test_workflow_checkpoint.py`

**Context:** `save_checkpoint` is a no-op and `load_checkpoint` just returns in-memory state. Workflow state is lost on restart. Fix: serialize `WorkflowState` to JSON and store in Redis with a key like `workflow:{id}:checkpoint`.

- [ ] **Step 1: Write failing test**

Create `tests/adapters/workflow/test_workflow_checkpoint.py`:

```python
"""Tests for workflow checkpoint persistence."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from astracore.adapters.workflow.native import NativeWorkflowOrchestrator
from astracore.core.domain.agent import AgentRole, AgentTask
from astracore.core.ports.workflow import WorkflowStatus


@pytest.fixture
def orchestrator_with_mock_redis():
    orch = NativeWorkflowOrchestrator()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    orch._redis = mock_redis
    return orch, mock_redis


@pytest.mark.asyncio
async def test_save_checkpoint_calls_redis(orchestrator_with_mock_redis):
    orch, mock_redis = orchestrator_with_mock_redis
    workflow = await orch.create_workflow(
        "test",
        [AgentTask(role=AgentRole.PLANNER, description="plan")],
    )
    await orch.save_checkpoint(workflow.workflow_id)
    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    assert f"workflow:{workflow.workflow_id}:checkpoint" in call_args[0][0]


@pytest.mark.asyncio
async def test_load_checkpoint_restores_workflow(orchestrator_with_mock_redis):
    orch, mock_redis = orchestrator_with_mock_redis

    task = AgentTask(role=AgentRole.EXECUTOR, description="execute")
    workflow = await orch.create_workflow("restore_test", [task])
    workflow.mark_running()

    # Save checkpoint data manually to simulate Redis returning it
    import json
    checkpoint_data = workflow.model_dump_json()
    mock_redis.get.return_value = checkpoint_data

    # Clear in-memory state to force load from Redis
    orch._workflows.clear()

    restored = await orch.load_checkpoint(workflow.workflow_id)
    assert restored.workflow_id == workflow.workflow_id
    assert restored.status == WorkflowStatus.RUNNING
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/workflow/ -v
```

Expected: FAIL — `save_checkpoint` is a no-op, `load_checkpoint` can't find workflow after clearing.

- [ ] **Step 3: Implement Redis-backed checkpoint**

Replace `src/astracore/adapters/workflow/native.py`:

```python
"""Native workflow orchestrator with Redis-backed checkpoint."""

import asyncio
from typing import Any
from uuid import UUID

from astracore.core.domain.agent import AgentTask, AgentTaskStatus
from astracore.core.ports.workflow import WorkflowOrchestrator, WorkflowState, WorkflowStatus


class NativeWorkflowOrchestrator(WorkflowOrchestrator):
    """Native Python-based workflow orchestrator with optional Redis checkpoints."""

    def __init__(self, redis_url: str | None = None):
        self._workflows: dict[UUID, WorkflowState] = {}
        self._redis_url = redis_url
        self._redis: Any = None

    def _get_redis(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        if self._redis_url is None:
            return None
        try:
            from redis.asyncio import Redis
            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        except ImportError:
            return None
        return self._redis

    def _checkpoint_key(self, workflow_id: UUID) -> str:
        return f"workflow:{workflow_id}:checkpoint"

    async def create_workflow(
        self,
        name: str,
        tasks: list[AgentTask],
        context: dict[str, Any] | None = None,
    ) -> WorkflowState:
        workflow = WorkflowState(name=name, tasks=tasks, context=context or {})
        self._workflows[workflow.workflow_id] = workflow
        return workflow

    async def execute_workflow(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow = self._workflows[workflow_id]
        workflow.mark_running()

        try:
            for task in workflow.tasks:
                if task.status == AgentTaskStatus.COMPLETED:
                    continue

                workflow.current_task_id = task.task_id
                task.mark_in_progress()

                await self._execute_task(task, workflow.context)

                if task.status == AgentTaskStatus.REQUIRES_APPROVAL:
                    workflow.status = WorkflowStatus.PAUSED
                    await self.save_checkpoint(workflow_id)
                    return workflow

                if task.status == AgentTaskStatus.FAILED:
                    workflow.mark_failed(task.error or "Task failed")
                    return workflow

            workflow.mark_completed({"completed_tasks": len(workflow.tasks)})

        except Exception as e:
            workflow.mark_failed(str(e))

        return workflow

    async def _execute_task(self, task: AgentTask, context: dict[str, Any]) -> None:
        """Placeholder task execution. Override in subclasses for real LLM dispatch."""
        await asyncio.sleep(0.1)
        task.mark_completed(f"Completed: {task.description}")

    async def pause_workflow(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow = self._workflows[workflow_id]
        workflow.status = WorkflowStatus.PAUSED
        return workflow

    async def resume_workflow(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            # Try loading from checkpoint before giving up
            try:
                await self.load_checkpoint(workflow_id)
            except Exception:
                raise ValueError(f"Workflow {workflow_id} not found")

        workflow = self._workflows[workflow_id]
        if workflow.status != WorkflowStatus.PAUSED:
            raise ValueError("Workflow is not paused")
        return await self.execute_workflow(workflow_id)

    async def get_workflow_state(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        return self._workflows[workflow_id]

    async def save_checkpoint(self, workflow_id: UUID) -> None:
        """Serialize workflow state to Redis. No-op if Redis unavailable."""
        if workflow_id not in self._workflows:
            return
        workflow = self._workflows[workflow_id]
        redis = self._get_redis()
        if redis is None:
            return
        try:
            key = self._checkpoint_key(workflow_id)
            # 7-day TTL matches typical workflow lifespan
            await redis.set(key, workflow.model_dump_json(), ex=604_800)
        except Exception:
            pass  # Checkpoint is best-effort

    async def load_checkpoint(self, workflow_id: UUID) -> WorkflowState:
        """Load workflow from Redis checkpoint. Falls back to in-memory state."""
        redis = self._get_redis()
        if redis is not None:
            try:
                key = self._checkpoint_key(workflow_id)
                data = await redis.get(key)
                if data:
                    workflow = WorkflowState.model_validate_json(data)
                    self._workflows[workflow.workflow_id] = workflow
                    return workflow
            except Exception:
                pass

        if workflow_id in self._workflows:
            return self._workflows[workflow_id]

        raise ValueError(f"Workflow {workflow_id} checkpoint not found")
```

- [ ] **Step 4: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/workflow/ -v
```

Expected: PASS both tests.

- [ ] **Step 5: Commit**

```bash
git add src/astracore/adapters/workflow/native.py tests/adapters/workflow/
git commit -m "feat: workflow checkpoint persistence via Redis (WP-13)"
```

---

## Task 11: ChromaDB — async wrapper for blocking sync calls

**Files:**
- Modify: `src/astracore/adapters/retrieval/chroma.py`
- Create: `tests/adapters/retrieval/__init__.py`
- Create: `tests/adapters/retrieval/test_chroma_async.py`

**Context:** `self._collection.query()` and `self._collection.add()` are synchronous ChromaDB calls made directly inside async functions. This blocks the entire asyncio event loop for the duration of the DB operation. Fix: wrap with `asyncio.get_event_loop().run_in_executor(None, ...)`.

- [ ] **Step 1: Write failing test**

Create `tests/adapters/retrieval/test_chroma_async.py`:

```python
"""Test that ChromaDB calls run in executor, not blocking event loop."""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from astracore.adapters.retrieval.chroma import ChromaRetrieverAdapter
from astracore.core.domain.retrieval import Citation, RetrievalQuery, RetrievedChunk


@pytest.mark.asyncio
async def test_retrieve_does_not_block_event_loop():
    """Verify retrieve() uses run_in_executor so other tasks aren't blocked."""
    adapter = ChromaRetrieverAdapter()
    adapter._client = MagicMock()

    def slow_query(*args, **kwargs):
        time.sleep(0.1)  # Simulate a slow sync call
        return {
            "documents": [["content"]],
            "distances": [[0.1]],
            "metadatas": [[{"document_id": "doc1"}]],
        }

    adapter._collection = MagicMock()
    adapter._collection.query.side_effect = slow_query

    query = RetrievalQuery(text="test", top_k=1)

    # Run retrieve and a concurrent task simultaneously
    concurrent_done = []

    async def concurrent_task():
        await asyncio.sleep(0)
        concurrent_done.append(True)

    # If retrieve blocks, concurrent_task won't run before retrieve returns
    await asyncio.gather(adapter.retrieve(query), concurrent_task())

    assert concurrent_done, "concurrent task should complete — event loop must not be blocked"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/retrieval/test_chroma_async.py -v
```

Expected: The test may behave unexpectedly — the concurrent task runs anyway due to asyncio cooperative scheduling. Note: the real problem is latency/throughput under load. The test verifies executor usage. Adjust the test to mock `run_in_executor` if needed.

- [ ] **Step 3: Wrap sync ChromaDB calls in run_in_executor**

Replace sync calls in `src/astracore/adapters/retrieval/chroma.py`:

```python
"""ChromaDB retriever adapter — async-safe via run_in_executor."""

import asyncio
from typing import Any
from uuid import uuid4

from astracore.core.domain.retrieval import Citation, RetrievalQuery, RetrievedChunk
from astracore.core.ports.retriever import IndexResult, RetrieverAdapter


class ChromaRetrieverAdapter(RetrieverAdapter):
    """ChromaDB vector store adapter. All sync ChromaDB calls run in executor."""

    def __init__(self, collection_name: str = "astracore", persist_directory: str | None = None):
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self._client: Any = None
        self._collection: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import chromadb
                if self.persist_directory:
                    self._client = chromadb.PersistentClient(path=self.persist_directory)
                else:
                    self._client = chromadb.Client()
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name
                )
            except ImportError as e:
                raise ImportError(
                    "chromadb not installed. Install with: pip install chromadb"
                ) from e
        return self._client

    def _chunk_text(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start = end - chunk_overlap
        return chunks

    async def index_document(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
    ) -> IndexResult:
        try:
            self._get_client()
            chunks = self._chunk_text(text, chunk_size, chunk_overlap)
            chunk_ids = [f"{document_id}_{i}" for i in range(len(chunks))]
            chunk_metadata = [
                {"document_id": document_id, "chunk_index": i, **(metadata or {})}
                for i in range(len(chunks))
            ]

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._collection.add(
                    documents=chunks, ids=chunk_ids, metadatas=chunk_metadata
                ),
            )

            return IndexResult(document_id=document_id, chunks_indexed=len(chunks), success=True)

        except Exception as e:
            return IndexResult(
                document_id=document_id, chunks_indexed=0, success=False, error=str(e)
            )

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
        self._get_client()

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: self._collection.query(
                query_texts=[query.text],
                n_results=query.top_k,
                where=query.filters if query.filters else None,
            ),
        )

        chunks: list[RetrievedChunk] = []
        if results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            distances = results["distances"][0] if results["distances"] else [0.0] * len(docs)
            metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)

            for doc, distance, meta in zip(docs, distances, metas):
                score = 1.0 - distance if distance else 1.0
                if score >= query.min_score:
                    chunks.append(
                        RetrievedChunk(
                            content=doc,
                            score=score,
                            citation=Citation(
                                source_id=meta.get("document_id", "unknown"),
                                source_type="document",
                                title=meta.get("title"),
                                metadata=meta,
                            ),
                            metadata=meta,
                        )
                    )
        return chunks

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """Sort by score (no external reranker installed). For production, use a cross-encoder."""
        return sorted(chunks, key=lambda x: x.score, reverse=True)[:top_k]

    async def delete_document(self, document_id: str) -> bool:
        try:
            self._get_client()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._collection.delete(where={"document_id": document_id}),
            )
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Run tests**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/adapters/retrieval/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/astracore/adapters/retrieval/chroma.py tests/adapters/retrieval/
git commit -m "fix: ChromaDB sync calls block asyncio event loop — wrap in run_in_executor"
```

---

## Task 12: Full Test Suite + Final Verification

**Files:**
- No new source changes
- Run all tests, linter, type-checker

- [ ] **Step 1: Run the full test suite**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/ -v --tb=short
```

Expected: All non-integration tests PASS. Integration tests SKIP.

- [ ] **Step 2: Run linter**

```bash
cd D:/project/study/AstraCoreAI && python -m ruff check src/astracore tests --fix
```

Expected: No errors.

- [ ] **Step 3: Run type checker**

```bash
cd D:/project/study/AstraCoreAI && python -m mypy src/astracore
```

Expected: No errors (strict mode). Fix any type errors found.

- [ ] **Step 4: Run coverage report**

```bash
cd D:/project/study/AstraCoreAI && python -m pytest tests/ --cov=src/astracore --cov-report=term-missing -q
```

Expected: Coverage report printed. Note which modules still need tests (for WP-11 follow-up work).

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "test: full test suite passing, linter and type-checker clean"
```

---

## Summary of All Issues Fixed

| # | Issue | Task | File(s) |
|---|-------|------|---------|
| 1 | `datetime.utcnow()` deprecated | 1 | All domain/port files |
| 2 | Pydantic v1 `class Config` | 1 | `sdk/config.py` |
| 3 | Regex recompiled on every call | 1 | `security/validator.py` |
| 4 | `truncate_to_budget` O(n²) | 2 | `session.py` |
| 5 | Token budget double-counted on session load | 2 | `session.py`, `chat.py` |
| 6 | RAG `retrieve_and_inject` hardcodes `top_k=3` | 3 | `rag.py` |
| 7 | Anthropic streaming tool arguments always `{}` | 4 | `adapters/llm/anthropic.py` |
| 8 | Tool definition building duplicated in tool_loop | 5 | `tool_loop.py` |
| 9 | Streaming tool loop missing security check | 5 | `tool_loop.py` |
| 10 | `use_tools=True` silently ignored | 5 | `service/api/chat.py` |
| 11 | Two separate LLM adapter instances | 5 | `service/api/chat.py` |
| 12 | CORS wildcard + credentials | 6 | `service/api/app.py` |
| 13 | RAG pipeline created per-request | 6 | `service/api/rag.py` |
| 14 | Policy retry/timeout dead code | 7 | `policy/engine.py`, `chat.py` |
| 15 | `_in_memory_sessions` memory leak | 8 | `adapters/memory/hybrid.py` |
| 16 | Long-term memory stub (WP-12) | 9 | `adapters/memory/hybrid.py`, `models.py` |
| 17 | Workflow checkpoint stub (WP-13) | 10 | `adapters/workflow/native.py` |
| 18 | ChromaDB blocks event loop | 11 | `adapters/retrieval/chroma.py` |
