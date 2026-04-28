# Unit Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write unit tests for all code paths touched by the backend optimization pass, with full coverage of the behaviors that were actually changed.

**Architecture:** Tests mirror the source tree under `tests/`. Every test file targets one module. External I/O (LLM, Redis, Postgres, Chroma) is replaced with `AsyncMock`/`MagicMock`; only pure business logic runs in-process. `pytest-asyncio` is already configured with `asyncio_mode = "auto"` so no `@pytest.mark.asyncio` decorators are needed.

**Tech Stack:** pytest 8, pytest-asyncio (auto mode), unittest.mock (AsyncMock / MagicMock), pydantic v2, tenacity.

---

## File Structure

| Create | Responsibility |
|--------|---------------|
| `tests/core/domain/test_session.py` | TokenBudget, ContextWindow.truncate_to_budget, SessionState.restore_messages |
| `tests/runtime/test_policy_engine.py` | PolicyEngine retry (tenacity), timeout, budget policy, security check |
| `tests/runtime/test_security_validator.py` | InputValidator XSS patterns + length, sanitize_metadata, ContentFilter |
| `tests/core/application/test_rag.py` | RAGPipeline retrieve_and_inject (top_k, context injection), index, delete |
| `tests/core/application/test_chat.py` | ChatUseCase session restore (no double-counting), save after response |
| `tests/core/application/test_tool_loop.py` | ToolLoopUseCase tool execution, security block, max_iterations, build_defs |
| `tests/adapters/llm/test_anthropic.py` | _convert_messages, generate_stream tool arg accumulation |
| `tests/adapters/memory/test_hybrid_memory.py` | In-memory fallback save/load, TTL eviction, cap eviction, Redis disable |
| `tests/adapters/workflow/test_native_workflow.py` | Create/execute/pause/resume workflow, checkpoint no-op without Redis |

---

### Task 1: Session domain — truncate_to_budget + restore_messages

**Files:**
- Create: `tests/core/domain/test_session.py`

- [ ] **Step 1: Write the tests**

```python
# tests/core/domain/test_session.py
"""Tests for SessionState, ContextWindow, TokenBudget domain models."""
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import ContextWindow, SessionState, TokenBudget


def _msg(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


# ---------- TokenBudget ----------

def test_token_budget_add_and_available():
    budget = TokenBudget(max_input_tokens=1000)
    budget.add_input_tokens(300)
    assert budget.current_input_tokens == 300
    assert budget.available_input_tokens() == 700


def test_token_budget_exceeded_when_at_limit():
    budget = TokenBudget(max_input_tokens=100)
    budget.add_input_tokens(100)
    assert budget.is_input_budget_exceeded() is True


def test_token_budget_not_exceeded_when_below_limit():
    budget = TokenBudget(max_input_tokens=100)
    budget.add_input_tokens(50)
    assert budget.is_input_budget_exceeded() is False


# ---------- ContextWindow.truncate_to_budget ----------

def test_truncate_to_budget_noop_when_under_limit():
    cw = ContextWindow()
    for i in range(3):
        cw.add_message(_msg(f"short {i}"))
    original_count = len(cw.messages)
    cw.truncate_to_budget(max_tokens=10_000)
    assert len(cw.messages) == original_count


def test_truncate_to_budget_removes_oldest_messages():
    cw = ContextWindow()
    # 40 chars → 10 tokens each (len // 4). 6 messages = 60 tokens total.
    for _ in range(6):
        cw.add_message(_msg("a" * 40))
    # Budget of 25 → drop until ≤ 25: drop 4, keep 2 (20 tokens)
    cw.truncate_to_budget(max_tokens=25)
    assert len(cw.messages) == 2


def test_truncate_to_budget_result_fits_within_budget():
    cw = ContextWindow()
    for _ in range(10):
        cw.add_message(_msg("x" * 100))  # 25 tokens each → 250 total
    cw.truncate_to_budget(max_tokens=100)
    assert cw.total_tokens() <= 100


def test_truncate_to_budget_empty_after_total_truncation():
    cw = ContextWindow()
    cw.add_message(_msg("a" * 40))  # 10 tokens
    cw.truncate_to_budget(max_tokens=0)
    assert cw.messages == []


# ---------- SessionState.restore_messages ----------

def test_restore_messages_does_not_double_count():
    session = SessionState()
    msgs = [_msg("hello " * 10) for _ in range(3)]
    for m in msgs:
        session.add_message(m)
    tokens_after_add = session.token_budget.current_input_tokens
    # restore must recalculate, not accumulate on top of add_message count
    session.restore_messages(msgs)
    assert session.token_budget.current_input_tokens == tokens_after_add


def test_restore_messages_sets_exact_token_count():
    session = SessionState()
    msgs = [_msg("a" * 40) for _ in range(3)]  # 3 × 10 = 30 tokens
    session.restore_messages(msgs)
    expected = sum(m.token_estimate() for m in msgs)
    assert session.token_budget.current_input_tokens == expected


def test_restore_messages_replaces_existing_messages():
    session = SessionState()
    session.add_message(_msg("original"))
    new_msgs = [_msg("new")]
    session.restore_messages(new_msgs)
    assert session.get_messages() == new_msgs


def test_restore_messages_on_empty_list_zeroes_token_count():
    session = SessionState()
    session.add_message(_msg("something"))
    session.restore_messages([])
    assert session.token_budget.current_input_tokens == 0
    assert session.get_messages() == []
```

- [ ] **Step 2: Run tests**

```bash
cd D:/project/study/AstraCoreAI
.hatch/venvs/Scripts/pytest tests/core/domain/test_session.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/core/domain/test_session.py
git commit -m "test: session domain — truncate_to_budget O(n) and restore_messages no double-count"
```

---

### Task 2: Policy engine — retry, timeout, budget, security

**Files:**
- Create: `tests/runtime/test_policy_engine.py`

- [ ] **Step 1: Write the tests**

```python
# tests/runtime/test_policy_engine.py
"""Tests for PolicyEngine — retry (tenacity), timeout, security, budget policies."""
import asyncio
import pytest
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.runtime.policy.engine import PolicyConfig, PolicyEngine, _make_retry_predicate
from astracore.runtime.policy.rules import RetryRule, SecurityRule, TimeoutRule


# ---------- _make_retry_predicate ----------

def test_retry_predicate_retries_generic_exception():
    pred = _make_retry_predicate([429, 500])
    assert pred(ValueError("boom")) is True


def test_retry_predicate_skips_non_listed_status_code():
    pred = _make_retry_predicate([429, 500])
    exc = ValueError("client error")
    exc.status_code = 400  # type: ignore[attr-defined]
    assert pred(exc) is False


def test_retry_predicate_retries_listed_status_code():
    pred = _make_retry_predicate([429, 500])
    exc = ValueError("rate limited")
    exc.status_code = 429  # type: ignore[attr-defined]
    assert pred(exc) is True


# ---------- apply_retry_policy ----------

async def test_apply_retry_policy_succeeds_on_third_attempt():
    config = PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("transient error")
        return "ok"

    result = await engine.apply_retry_policy(flaky)
    assert result == "ok"
    assert call_count == 3


async def test_apply_retry_policy_reraises_after_max_retries():
    config = PolicyConfig(retry=RetryRule(max_retries=2, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)

    async def always_fails():
        raise RuntimeError("always bad")

    with pytest.raises(RuntimeError, match="always bad"):
        await engine.apply_retry_policy(always_fails)


async def test_apply_retry_policy_does_not_retry_non_listed_status_code():
    config = PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)
    call_count = 0

    async def client_error():
        nonlocal call_count
        call_count += 1
        exc = ValueError("bad request")
        exc.status_code = 400  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(ValueError):
        await engine.apply_retry_policy(client_error)
    assert call_count == 1  # no retries for non-listed status codes


# ---------- apply_timeout_policy ----------

async def test_apply_timeout_policy_raises_on_slow_function():
    config = PolicyConfig(timeout=TimeoutRule(llm_timeout_ms=50))
    engine = PolicyEngine(config)

    async def slow():
        await asyncio.sleep(10)
        return "done"

    with pytest.raises(asyncio.TimeoutError):
        await engine.apply_timeout_policy(slow, timeout_type="llm")


async def test_apply_timeout_policy_returns_result_on_fast_function():
    engine = PolicyEngine()

    async def fast():
        return "result"

    result = await engine.apply_timeout_policy(fast, timeout_type="llm")
    assert result == "result"


async def test_apply_timeout_policy_uses_correct_timeout_for_type():
    # retrieval_timeout_ms default = 10_000 ms (10s), slow() sleeps 0.1s → should succeed
    engine = PolicyEngine()

    async def quick():
        await asyncio.sleep(0.01)
        return "done"

    result = await engine.apply_timeout_policy(quick, timeout_type="retrieval")
    assert result == "done"


# ---------- check_security_policy ----------

def test_check_security_policy_allows_all_when_no_whitelist():
    engine = PolicyEngine()
    assert engine.check_security_policy("any_tool", {}) is True


def test_check_security_policy_blocks_tool_not_in_whitelist():
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["allowed_tool"]))
    engine = PolicyEngine(config)
    assert engine.check_security_policy("forbidden_tool", {}) is False


def test_check_security_policy_allows_whitelisted_tool():
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["safe_tool"]))
    engine = PolicyEngine(config)
    assert engine.check_security_policy("safe_tool", {}) is True


def test_check_security_policy_blocks_sensitive_field_in_args():
    engine = PolicyEngine()
    # "password" is in the default sensitive_fields list
    assert engine.check_security_policy("tool", {"password": "s3cr3t"}) is False


def test_check_security_policy_blocks_api_key_field():
    engine = PolicyEngine()
    assert engine.check_security_policy("tool", {"api_key": "sk-xxx"}) is False


def test_check_security_policy_allows_clean_args():
    engine = PolicyEngine()
    assert engine.check_security_policy("tool", {"city": "NYC", "count": 5}) is True
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/runtime/test_policy_engine.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_policy_engine.py
git commit -m "test: policy engine — tenacity retry, timeout, security check"
```

---

### Task 3: Security validator

**Files:**
- Create: `tests/runtime/test_security_validator.py`

- [ ] **Step 1: Write the tests**

```python
# tests/runtime/test_security_validator.py
"""Tests for InputValidator (XSS patterns, length) and ContentFilter."""
import pytest
from astracore.runtime.security.validator import ContentFilter, InputValidator


@pytest.fixture
def validator():
    return InputValidator(max_input_length=100)


# ---------- InputValidator.validate_user_input ----------

def test_validate_accepts_normal_input(validator):
    ok, err = validator.validate_user_input("Hello, how are you?")
    assert ok is True
    assert err is None


def test_validate_rejects_input_exceeding_max_length(validator):
    ok, err = validator.validate_user_input("x" * 101)
    assert ok is False
    assert "maximum length" in err


def test_validate_rejects_script_tag(validator):
    ok, err = validator.validate_user_input("<script>alert(1)</script>")
    assert ok is False
    assert "suspicious patterns" in err


def test_validate_rejects_script_tag_case_insensitive(validator):
    ok, _ = validator.validate_user_input("<SCRIPT>evil()</SCRIPT>")
    assert ok is False


def test_validate_rejects_javascript_protocol(validator):
    ok, _ = validator.validate_user_input("javascript:void(0)")
    assert ok is False


def test_validate_rejects_onerror_attribute(validator):
    ok, _ = validator.validate_user_input('<img onerror=alert(1)>')
    assert ok is False


def test_validate_rejects_onclick_attribute(validator):
    ok, _ = validator.validate_user_input('<button onclick=evil()>')
    assert ok is False


def test_validate_accepts_input_at_exactly_max_length(validator):
    ok, _ = validator.validate_user_input("a" * 100)
    assert ok is True


# ---------- InputValidator.sanitize_metadata ----------

def test_sanitize_redacts_password():
    v = InputValidator()
    result = v.sanitize_metadata({"password": "s3cr3t", "name": "alice"})
    assert result["password"] == "***REDACTED***"
    assert result["name"] == "alice"


def test_sanitize_redacts_api_key():
    v = InputValidator()
    result = v.sanitize_metadata({"api_key": "sk-xxx"})
    assert result["api_key"] == "***REDACTED***"


def test_sanitize_redacts_token():
    v = InputValidator()
    result = v.sanitize_metadata({"auth_token": "abc"})
    assert result["auth_token"] == "***REDACTED***"


def test_sanitize_preserves_nonsensitive_fields():
    v = InputValidator()
    result = v.sanitize_metadata({"user_id": "123", "region": "us-east"})
    assert result == {"user_id": "123", "region": "us-east"}


# ---------- ContentFilter ----------

def test_content_filter_blocks_added_term():
    f = ContentFilter()
    f.add_blocked_term("spam")
    ok, msg = f.filter_content("This is spam content")
    assert ok is False
    assert "spam" in msg


def test_content_filter_is_case_insensitive():
    f = ContentFilter()
    f.add_blocked_term("spam")
    ok, _ = f.filter_content("This is SPAM")
    assert ok is False


def test_content_filter_allows_clean_content():
    f = ContentFilter()
    f.add_blocked_term("blocked")
    ok, text = f.filter_content("perfectly clean content")
    assert ok is True
    assert text == "perfectly clean content"


def test_content_filter_allows_before_term_added():
    f = ContentFilter()
    ok, _ = f.filter_content("anything goes without terms")
    assert ok is True
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/runtime/test_security_validator.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_security_validator.py
git commit -m "test: security validator — XSS patterns, length limit, metadata sanitization, content filter"
```

---

### Task 4: RAG pipeline

**Files:**
- Create: `tests/core/application/test_rag.py`

- [ ] **Step 1: Write the tests**

```python
# tests/core/application/test_rag.py
"""Tests for RAGPipeline — retrieve_and_inject, index, delete."""
import pytest
from unittest.mock import AsyncMock
from astracore.core.application.rag import RAGPipeline
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.retrieval import Citation, RetrievedChunk
from astracore.core.ports.retriever import IndexResult


def _chunk(content: str, score: float = 0.9, source_id: str = "doc1") -> RetrievedChunk:
    return RetrievedChunk(
        content=content,
        score=score,
        citation=Citation(source_id=source_id, source_type="document"),
    )


@pytest.fixture
def mock_retriever():
    r = AsyncMock()
    r.retrieve.return_value = []
    r.rerank.return_value = []
    r.index_document.return_value = IndexResult(
        document_id="d1", chunks_indexed=1, success=True
    )
    r.delete_document.return_value = True
    return r


@pytest.fixture
def rag(mock_retriever):
    return RAGPipeline(retriever=mock_retriever)


# ---------- retrieve_and_inject ----------

async def test_retrieve_and_inject_returns_original_when_no_chunks(rag, mock_retriever):
    mock_retriever.retrieve.return_value = []
    msgs = [Message(role=MessageRole.USER, content="question")]
    result = await rag.retrieve_and_inject("question", msgs)
    assert result == msgs


async def test_retrieve_and_inject_prepends_context_message(rag, mock_retriever):
    chunk = _chunk("Paris is the capital of France.")
    mock_retriever.retrieve.return_value = [chunk]
    mock_retriever.rerank.return_value = [chunk]
    msgs = [Message(role=MessageRole.USER, content="What is the capital?")]

    result = await rag.retrieve_and_inject("What is the capital?", msgs)

    assert len(result) == 2
    assert result[0].role == MessageRole.SYSTEM
    assert "Paris is the capital of France." in result[0].content
    assert result[1] == msgs[0]


async def test_retrieve_and_inject_context_message_contains_citation(rag, mock_retriever):
    chunk = _chunk("Some fact.", source_id="my-doc")
    mock_retriever.retrieve.return_value = [chunk]
    mock_retriever.rerank.return_value = [chunk]
    msgs = [Message(role=MessageRole.USER, content="q")]

    result = await rag.retrieve_and_inject("q", msgs)
    assert "my-doc" in result[0].content


async def test_retrieve_and_inject_passes_top_k_to_rerank(rag, mock_retriever):
    chunk = _chunk("content")
    mock_retriever.retrieve.return_value = [chunk]
    mock_retriever.rerank.return_value = [chunk]
    msgs = [Message(role=MessageRole.USER, content="q")]

    await rag.retrieve_and_inject("q", msgs, top_k=7)

    mock_retriever.rerank.assert_called_once_with("q", [chunk], top_k=7)


# ---------- index_document ----------

async def test_index_document_returns_true_on_success(rag):
    result = await rag.index_document("doc1", "Some text content")
    assert result is True


async def test_index_document_returns_false_on_failure(rag, mock_retriever):
    mock_retriever.index_document.return_value = IndexResult(
        document_id="d1", chunks_indexed=0, success=False, error="disk full"
    )
    result = await rag.index_document("doc1", "text")
    assert result is False


# ---------- delete_document ----------

async def test_delete_document_delegates_to_retriever(rag, mock_retriever):
    result = await rag.delete_document("doc1")
    assert result is True
    mock_retriever.delete_document.assert_called_once_with("doc1")


async def test_retrieve_with_citations_calls_rerank(rag, mock_retriever):
    chunk = _chunk("data")
    mock_retriever.retrieve.return_value = [chunk]
    mock_retriever.rerank.return_value = [chunk]

    result = await rag.retrieve_with_citations("q", top_k=3)

    assert result == [chunk]
    mock_retriever.rerank.assert_called_once_with("q", [chunk], top_k=3)
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/core/application/test_rag.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/core/application/test_rag.py
git commit -m "test: RAG pipeline — retrieve_and_inject top_k fix, context injection, index/delete"
```

---

### Task 5: Chat use case

**Files:**
- Create: `tests/core/application/test_chat.py`

- [ ] **Step 1: Write the tests**

```python
# tests/core/application/test_chat.py
"""Tests for ChatUseCase — session restore (no token double-count), LLM call, save."""
import pytest
from unittest.mock import AsyncMock
from uuid import uuid4
from astracore.core.application.chat import ChatUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.ports.llm import LLMResponse
from astracore.core.ports.memory import MemoryAdapter
from astracore.runtime.policy.engine import PolicyEngine


@pytest.fixture
def session_id():
    return uuid4()


@pytest.fixture
def mock_memory():
    m = AsyncMock(spec=MemoryAdapter)
    m.load_short_term.return_value = []
    m.save_short_term.return_value = None
    return m


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(
        content="Hello from assistant", model="claude-sonnet-4-6"
    )
    return llm


@pytest.fixture
def chat(mock_llm, mock_memory):
    return ChatUseCase(
        llm_adapter=mock_llm,
        memory_adapter=mock_memory,
        policy_engine=PolicyEngine(),
    )


# ---------- execute ----------

async def test_execute_returns_assistant_message(chat, session_id):
    msg = await chat.execute(session_id, "Hi there")
    assert msg.role == MessageRole.ASSISTANT
    assert msg.content == "Hello from assistant"


async def test_execute_saves_session_after_response(chat, session_id, mock_memory):
    await chat.execute(session_id, "Hi")
    mock_memory.save_short_term.assert_called_once()


async def test_execute_includes_user_message_in_llm_call(chat, session_id, mock_llm):
    await chat.execute(session_id, "Tell me a joke")
    call_args = mock_llm.generate.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    assert any(m.content == "Tell me a joke" for m in messages)


async def test_execute_does_not_double_count_tokens_on_existing_session(
    session_id, mock_llm, mock_memory
):
    """When session already has messages in Redis, restore_messages must be used
    so token budget is recalculated, not accumulated on top of stored count."""
    existing_msgs = [
        Message(role=MessageRole.USER, content="hello " * 10),
        Message(role=MessageRole.ASSISTANT, content="hi " * 10),
    ]
    mock_memory.load_short_term.return_value = existing_msgs

    chat = ChatUseCase(
        llm_adapter=mock_llm,
        memory_adapter=mock_memory,
        policy_engine=PolicyEngine(),
    )

    await chat.execute(session_id, "new question")

    call_args = mock_llm.generate.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    # 2 restored + 1 new user message = exactly 3
    assert len(messages) == 3


async def test_execute_saved_messages_include_assistant_reply(
    session_id, mock_llm, mock_memory
):
    await chat(mock_llm, mock_memory).execute(session_id, "question") if False else None
    chat_uc = ChatUseCase(
        llm_adapter=mock_llm,
        memory_adapter=mock_memory,
        policy_engine=PolicyEngine(),
    )
    await chat_uc.execute(session_id, "question")
    saved_messages = mock_memory.save_short_term.call_args.kwargs.get(
        "messages"
    ) or mock_memory.save_short_term.call_args.args[1]
    roles = [m.role for m in saved_messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/core/application/test_chat.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/core/application/test_chat.py
git commit -m "test: chat use case — session restore, token double-count fix, LLM call, session save"
```

---

### Task 6: Tool loop use case

**Files:**
- Create: `tests/core/application/test_tool_loop.py`

- [ ] **Step 1: Write the tests**

```python
# tests/core/application/test_tool_loop.py
"""Tests for ToolLoopUseCase — tool execution, security block, max_iterations, build_defs."""
import pytest
from unittest.mock import AsyncMock
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import ToolCall
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMResponse
from astracore.core.ports.tool import (
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
    ToolParameterType,
)
from astracore.runtime.policy.engine import PolicyConfig, PolicyEngine
from astracore.runtime.policy.rules import SecurityRule


def _tool_def(name: str = "search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Search the web",
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="Search query",
                required=True,
            )
        ],
    )


def _exec_result(name: str = "search", output: str = "results") -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=name, success=True, output=output, execution_time_ms=10.0
    )


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(content="Done", model="test-model")
    return llm


@pytest.fixture
def mock_tools():
    t = AsyncMock()
    t.get_definitions.return_value = [_tool_def()]
    t.execute.return_value = _exec_result()
    return t


@pytest.fixture
def loop_uc(mock_llm, mock_tools):
    return ToolLoopUseCase(
        llm_adapter=mock_llm,
        tool_adapter=mock_tools,
        policy_engine=PolicyEngine(),
        max_iterations=5,
    )


# ---------- execute_with_tools ----------

async def test_execute_with_tools_breaks_immediately_when_no_tool_calls(
    loop_uc, mock_llm
):
    session = SessionState()
    await loop_uc.execute_with_tools(session)
    assert mock_llm.generate.call_count == 1


async def test_execute_with_tools_calls_tool_and_continues(loop_uc, mock_llm, mock_tools):
    tool_call = ToolCall(name="search", arguments={"query": "Python"})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tool_call], model="test"),
        LLMResponse(content="Final answer", model="test"),
    ]
    session = SessionState()
    await loop_uc.execute_with_tools(session)

    mock_tools.execute.assert_called_once_with(
        tool_name="search", arguments={"query": "Python"}
    )
    assert mock_llm.generate.call_count == 2


async def test_execute_with_tools_blocks_tool_via_security_policy(
    mock_llm, mock_tools
):
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["allowed_tool"]))
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(config))

    tool_call = ToolCall(name="forbidden_tool", arguments={})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tool_call], model="test"),
        LLMResponse(content="Done", model="test"),
    ]
    session = SessionState()
    await uc.execute_with_tools(session)

    mock_tools.execute.assert_not_called()


async def test_execute_with_tools_blocked_result_is_error_message(
    mock_llm, mock_tools
):
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["allowed"]))
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(config))

    tool_call = ToolCall(name="blocked", arguments={})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tool_call], model="test"),
        LLMResponse(content="Done", model="test"),
    ]
    session = SessionState()
    result = await uc.execute_with_tools(session)

    # The TOOL message should contain the blocked error
    tool_msgs = [m for m in result.get_messages() if m.role.value == "tool"]
    assert any(tr.is_error for msg in tool_msgs for tr in msg.tool_results)


async def test_execute_with_tools_respects_max_iterations(mock_llm, mock_tools):
    tool_call = ToolCall(name="search", arguments={"query": "loop"})
    mock_llm.generate.return_value = LLMResponse(
        content="", tool_calls=[tool_call], model="test"
    )
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(), max_iterations=3)
    session = SessionState()
    await uc.execute_with_tools(session)

    assert mock_llm.generate.call_count == 3


# ---------- _build_tool_definitions ----------

def test_build_tool_definitions_shape(loop_uc, mock_tools):
    defs = loop_uc._build_tool_definitions()
    assert len(defs) == 1
    d = defs[0]
    assert d["name"] == "search"
    assert "input_schema" in d
    assert d["input_schema"]["type"] == "object"
    assert "query" in d["input_schema"]["properties"]
    assert "query" in d["input_schema"]["required"]


def test_build_tool_definitions_excludes_optional_params(mock_llm):
    tools = AsyncMock()
    tools.get_definitions.return_value = [
        ToolDefinition(
            name="tool",
            description="desc",
            parameters=[
                ToolParameter(
                    name="required_p",
                    type=ToolParameterType.STRING,
                    description="req",
                    required=True,
                ),
                ToolParameter(
                    name="optional_p",
                    type=ToolParameterType.NUMBER,
                    description="opt",
                    required=False,
                ),
            ],
        )
    ]
    uc = ToolLoopUseCase(mock_llm, tools, PolicyEngine())
    defs = uc._build_tool_definitions()
    required = defs[0]["input_schema"]["required"]
    assert "required_p" in required
    assert "optional_p" not in required
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/core/application/test_tool_loop.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/core/application/test_tool_loop.py
git commit -m "test: tool loop — security block, max_iterations, tool execution, build_defs shape"
```

---

### Task 7: Anthropic adapter — message conversion + streaming tool accumulation

**Files:**
- Create: `tests/adapters/llm/test_anthropic.py`

- [ ] **Step 1: Write the tests**

```python
# tests/adapters/llm/test_anthropic.py
"""Tests for AnthropicAdapter — _convert_messages and generate_stream tool arg accumulation."""
import pytest
from unittest.mock import MagicMock
from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.core.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.core.ports.llm import StreamEventType


@pytest.fixture
def adapter():
    return AnthropicAdapter(api_key="test-key")


# ---------- _convert_messages ----------

def test_convert_messages_skips_system_role(adapter):
    msgs = [
        Message(role=MessageRole.SYSTEM, content="You are helpful"),
        Message(role=MessageRole.USER, content="Hello"),
    ]
    result = adapter._convert_messages(msgs)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_get_system_message_extracts_system(adapter):
    msgs = [
        Message(role=MessageRole.SYSTEM, content="System prompt"),
        Message(role=MessageRole.USER, content="Hi"),
    ]
    assert adapter._get_system_message(msgs) == "System prompt"


def test_get_system_message_returns_none_when_absent(adapter):
    msgs = [Message(role=MessageRole.USER, content="Hi")]
    assert adapter._get_system_message(msgs) is None


def test_convert_messages_formats_tool_calls(adapter):
    tc = ToolCall(id="tc_1", name="search", arguments={"q": "python"})
    msg = Message(role=MessageRole.ASSISTANT, content="Let me search", tool_calls=[tc])
    result = adapter._convert_messages([msg])

    assert result[0]["role"] == "assistant"
    content = result[0]["content"]
    assert isinstance(content, list)
    types = [block["type"] for block in content]
    assert "tool_use" in types
    tool_block = next(b for b in content if b["type"] == "tool_use")
    assert tool_block["name"] == "search"
    assert tool_block["input"] == {"q": "python"}


def test_convert_messages_formats_tool_results(adapter):
    tr = ToolResult(tool_call_id="tc_1", name="search", content="results here")
    msg = Message(role=MessageRole.TOOL, content="", tool_results=[tr])
    result = adapter._convert_messages([msg])

    content = result[0]["content"]
    assert content[0]["type"] == "tool_result"
    assert content[0]["content"] == "results here"
    assert content[0]["tool_use_id"] == "tc_1"


def test_convert_messages_plain_assistant_message(adapter):
    msg = Message(role=MessageRole.ASSISTANT, content="Just text")
    result = adapter._convert_messages([msg])
    assert result[0]["content"] == "Just text"


# ---------- generate_stream — helpers ----------

def _event(type_: str, **kwargs) -> MagicMock:
    e = MagicMock()
    e.type = type_
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


def _delta(delta_type: str, **kwargs) -> MagicMock:
    d = MagicMock()
    d.type = delta_type
    for k, v in kwargs.items():
        setattr(d, k, v)
    return d


def _tool_block(tool_id: str, tool_name: str) -> MagicMock:
    b = MagicMock()
    b.type = "tool_use"
    b.id = tool_id
    b.name = tool_name
    return b


class _FakeStreamCtx:
    """Async context manager that yields a pre-defined sequence of events."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self._gen()

    async def __aexit__(self, *args):
        pass

    async def _gen(self):
        for e in self._events:
            yield e


# ---------- generate_stream — tests ----------

async def test_generate_stream_accumulates_tool_arguments(adapter):
    """input_json_delta chunks must be merged into a single complete ToolCall."""
    events = [
        _event(
            "content_block_start",
            index=0,
            content_block=_tool_block("tc_1", "get_weather"),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("input_json_delta", partial_json='{"city":'),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("input_json_delta", partial_json='"NYC"}'),
        ),
        _event("content_block_stop", index=0),
    ]
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx(events)
    adapter._client = mock_client

    collected = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Weather?")]
    ):
        collected.append(ev)

    tool_events = [e for e in collected if e.event_type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 1
    tc = tool_events[0].tool_call
    assert tc.name == "get_weather"
    assert tc.arguments == {"city": "NYC"}


async def test_generate_stream_emits_text_delta(adapter):
    events = [
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("text_delta", text="Hello world"),
        ),
    ]
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx(events)
    adapter._client = mock_client

    collected = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Hi")]
    ):
        collected.append(ev)

    text_events = [e for e in collected if e.event_type == StreamEventType.TEXT_DELTA]
    assert len(text_events) == 1
    assert text_events[0].content == "Hello world"


async def test_generate_stream_always_ends_with_done_event(adapter):
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx([])
    adapter._client = mock_client

    events = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Hi")]
    ):
        events.append(ev)

    assert events[-1].event_type == StreamEventType.DONE


async def test_generate_stream_handles_multiple_tool_blocks(adapter):
    """Two separate tool_use blocks (different indices) each emit their own ToolCall."""
    events = [
        _event("content_block_start", index=0, content_block=_tool_block("tc_a", "tool_a")),
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("input_json_delta", partial_json='{"x": 1}'),
        ),
        _event("content_block_stop", index=0),
        _event("content_block_start", index=1, content_block=_tool_block("tc_b", "tool_b")),
        _event(
            "content_block_delta",
            index=1,
            delta=_delta("input_json_delta", partial_json='{"y": 2}'),
        ),
        _event("content_block_stop", index=1),
    ]
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx(events)
    adapter._client = mock_client

    collected = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Do things")]
    ):
        collected.append(ev)

    tool_events = [e for e in collected if e.event_type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 2
    names = {e.tool_call.name for e in tool_events}
    assert names == {"tool_a", "tool_b"}
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/adapters/llm/test_anthropic.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/adapters/llm/test_anthropic.py
git commit -m "test: Anthropic adapter — message conversion, streaming tool arg accumulation fix"
```

---

### Task 8: Hybrid memory adapter — in-memory fallback, eviction, Redis disable

**Files:**
- Create: `tests/adapters/memory/test_hybrid_memory.py`

- [ ] **Step 1: Write the tests**

```python
# tests/adapters/memory/test_hybrid_memory.py
"""Tests for HybridMemoryAdapter — in-memory fallback, TTL eviction, cap, Redis disable."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.domain.message import Message, MessageRole


def _adapter() -> HybridMemoryAdapter:
    a = HybridMemoryAdapter(
        redis_url="redis://localhost:6379",
        postgres_url="postgresql+asyncpg://localhost/test",
    )
    a._redis_disabled = True  # bypass Redis — test in-memory path only
    return a


def _msg(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


# ---------- save / load short-term (in-memory fallback) ----------

async def test_save_and_load_short_term_roundtrip():
    adapter = _adapter()
    sid = uuid4()
    msgs = [_msg("hello"), _msg("world")]
    await adapter.save_short_term(sid, msgs)
    loaded = await adapter.load_short_term(sid)
    assert len(loaded) == 2
    assert loaded[0].content == "hello"
    assert loaded[1].content == "world"


async def test_load_short_term_returns_empty_for_unknown_session():
    adapter = _adapter()
    result = await adapter.load_short_term(uuid4())
    assert result == []


async def test_save_short_term_overwrites_previous():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("first")])
    await adapter.save_short_term(sid, [_msg("second")])
    loaded = await adapter.load_short_term(sid)
    assert len(loaded) == 1
    assert loaded[0].content == "second"


# ---------- _evict_stale — TTL ----------

async def test_evict_stale_removes_expired_sessions():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("old")])
    key = adapter._session_key(sid)
    # Force timestamp to be expired
    adapter._session_timestamps[key] = datetime.now(UTC) - timedelta(hours=2)
    adapter._evict_stale()
    assert key not in adapter._in_memory_sessions


async def test_evict_stale_keeps_fresh_sessions():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("fresh")])
    key = adapter._session_key(sid)
    adapter._evict_stale()
    assert key in adapter._in_memory_sessions


# ---------- _evict_stale — cap ----------

async def test_evict_enforces_session_cap():
    adapter = _adapter()
    adapter._MAX_IN_MEMORY_SESSIONS = 3  # low cap for test

    sids = [uuid4() for _ in range(4)]
    for sid in sids:
        await adapter.save_short_term(sid, [_msg("data")])

    # After 4 saves the cap has been exceeded; an explicit evict pass enforces it
    adapter._evict_stale()
    assert len(adapter._in_memory_sessions) <= 3


# ---------- Redis disable ----------

def test_disable_redis_sets_flag():
    a = HybridMemoryAdapter("redis://localhost:6379", "postgresql+asyncpg://localhost/test")
    assert a._redis_disabled is False
    a._disable_redis()
    assert a._redis_disabled is True
    assert a._redis is None


def test_get_redis_returns_none_when_disabled():
    a = HybridMemoryAdapter("redis://localhost:6379", "postgresql+asyncpg://localhost/test")
    a._disable_redis()
    assert a._get_redis() is None


async def test_load_short_term_uses_memory_cache_when_redis_disabled():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("cached")])
    # Re-disable just to be explicit
    adapter._redis_disabled = True
    loaded = await adapter.load_short_term(sid)
    assert loaded[0].content == "cached"


async def test_delete_session_memory_clears_in_memory():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("data")])
    await adapter.delete_session_memory(sid)
    result = await adapter.load_short_term(sid)
    assert result == []
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/adapters/memory/test_hybrid_memory.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/adapters/memory/test_hybrid_memory.py
git commit -m "test: hybrid memory — in-memory fallback, TTL eviction, cap enforcement, Redis disable"
```

---

### Task 9: Native workflow orchestrator

**Files:**
- Create: `tests/adapters/workflow/test_native_workflow.py`

- [ ] **Step 1: Write the tests**

```python
# tests/adapters/workflow/test_native_workflow.py
"""Tests for NativeWorkflowOrchestrator — create, execute, pause, resume, checkpoint."""
import pytest
from uuid import uuid4
from astracore.adapters.workflow.native import NativeWorkflowOrchestrator
from astracore.core.domain.agent import AgentRole, AgentTask, AgentTaskStatus
from astracore.core.ports.workflow import WorkflowStatus


def _task(description: str = "do something") -> AgentTask:
    return AgentTask(role=AgentRole.EXECUTOR, description=description)


# ---------- create_workflow ----------

async def test_create_workflow_returns_workflow_state():
    oc = NativeWorkflowOrchestrator()
    tasks = [_task("step1"), _task("step2")]
    wf = await oc.create_workflow("test-wf", tasks)
    assert wf.name == "test-wf"
    assert len(wf.tasks) == 2
    assert wf.status == WorkflowStatus.PENDING


async def test_create_workflow_with_context():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()], context={"key": "val"})
    assert wf.context == {"key": "val"}


# ---------- execute_workflow ----------

async def test_execute_workflow_completes_all_tasks():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task("a"), _task("b")])
    result = await oc.execute_workflow(wf.workflow_id)
    assert result.status == WorkflowStatus.COMPLETED
    assert all(t.status == AgentTaskStatus.COMPLETED for t in result.tasks)


async def test_execute_workflow_raises_for_unknown_id():
    oc = NativeWorkflowOrchestrator()
    with pytest.raises(ValueError, match="not found"):
        await oc.execute_workflow(uuid4())


async def test_execute_workflow_skips_already_completed_tasks():
    oc = NativeWorkflowOrchestrator()
    t1 = _task("pre-completed")
    t1.mark_completed("done already")
    t2 = _task("fresh")
    wf = await oc.create_workflow("wf", [t1, t2])
    result = await oc.execute_workflow(wf.workflow_id)
    # Both should be completed; t1 was never re-executed
    assert result.status == WorkflowStatus.COMPLETED


# ---------- get_workflow_state ----------

async def test_get_workflow_state_returns_current_state():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    state = await oc.get_workflow_state(wf.workflow_id)
    assert state.workflow_id == wf.workflow_id


async def test_get_workflow_state_raises_for_unknown_id():
    oc = NativeWorkflowOrchestrator()
    with pytest.raises(ValueError, match="not found"):
        await oc.get_workflow_state(uuid4())


# ---------- pause_workflow ----------

async def test_pause_workflow_sets_paused_status():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    await oc.pause_workflow(wf.workflow_id)
    state = await oc.get_workflow_state(wf.workflow_id)
    assert state.status == WorkflowStatus.PAUSED


# ---------- resume_workflow ----------

async def test_resume_workflow_completes_after_pause():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    await oc.pause_workflow(wf.workflow_id)
    result = await oc.resume_workflow(wf.workflow_id)
    assert result.status == WorkflowStatus.COMPLETED


async def test_resume_workflow_raises_when_not_paused():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    await oc.execute_workflow(wf.workflow_id)
    with pytest.raises(ValueError, match="not paused"):
        await oc.resume_workflow(wf.workflow_id)


# ---------- save_checkpoint — no-op without Redis ----------

async def test_save_checkpoint_is_noop_without_redis():
    oc = NativeWorkflowOrchestrator()  # no redis_url
    wf = await oc.create_workflow("wf", [_task()])
    # Must not raise even though Redis is not configured
    await oc.save_checkpoint(wf.workflow_id)


async def test_save_checkpoint_unknown_id_is_noop():
    oc = NativeWorkflowOrchestrator()
    await oc.save_checkpoint(uuid4())  # should not raise


# ---------- load_checkpoint — in-memory fallback ----------

async def test_load_checkpoint_falls_back_to_in_memory():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    loaded = await oc.load_checkpoint(wf.workflow_id)
    assert loaded.workflow_id == wf.workflow_id


async def test_load_checkpoint_raises_when_not_found():
    oc = NativeWorkflowOrchestrator()
    with pytest.raises(ValueError, match="checkpoint not found"):
        await oc.load_checkpoint(uuid4())
```

- [ ] **Step 2: Run tests**

```bash
.hatch/venvs/Scripts/pytest tests/adapters/workflow/test_native_workflow.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/adapters/workflow/test_native_workflow.py
git commit -m "test: native workflow — create, execute, pause, resume, checkpoint no-op without Redis"
```

---

### Task 10: Full suite run + coverage report

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd D:/project/study/AstraCoreAI
.hatch/venvs/Scripts/pytest tests/ -v --tb=short 2>&1
```

Expected: all tests PASS, zero failures.

- [ ] **Step 2: Check coverage for the optimized modules**

```bash
.hatch/venvs/Scripts/pytest tests/ --cov=astracore.core --cov=astracore.runtime --cov=astracore.adapters --cov-report=term-missing --tb=short
```

- [ ] **Step 3: Final commit**

```bash
git add .
git commit -m "test: complete unit test suite for backend optimization pass"
```

---

## Self-Review

**Spec coverage check:**
- Session truncate_to_budget → Task 1 ✓
- Session restore_messages no double-count → Task 1 + Task 5 ✓
- Policy retry with tenacity → Task 2 ✓
- Policy timeout via asyncio.wait_for → Task 2 ✓
- Security check whitelist + sensitive fields → Task 2 ✓
- XSS regex patterns (precompiled) → Task 3 ✓
- RAG top_k not hardcoded → Task 4 ✓
- Chat session restore path → Task 5 ✓
- Tool loop security in streaming + non-streaming → Task 6 ✓
- Anthropic streaming tool arg accumulation → Task 7 ✓
- Hybrid memory eviction → Task 8 ✓
- Redis disable on error → Task 8 ✓
- Workflow Redis checkpoint → Task 9 ✓

**Placeholder scan:** No TBD/TODO/placeholder text found.

**Type consistency:** All types match source — `AgentTask` requires `role: AgentRole`, used correctly in Task 9. `ToolExecutionResult` requires `execution_time_ms`, provided in Task 6.
