"""Memory engine and API regression tests."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from astracore.infrastructure.db.session import get_engine, init_db
from astracore.modules.chat.domain.message import Message
from astracore.shared.ports.llm import LLMAdapter, LLMResponse, StreamEvent


class _MemoryDecisionLLM(LLMAdapter):
    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def generate(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(content=self._payload, model=model or "fake")

    async def generate_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ):
        if False:
            yield StreamEvent(event_type="done")  # pragma: no cover

    async def count_tokens(self, messages: list[Message]) -> int:
        return 0

    def supports_tools(self) -> bool:
        return False


@pytest.fixture
async def memory_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"
    get_engine.cache_clear()
    await init_db(db_url)

    from astracore.modules.chat import conversations_api
    from astracore.modules.memory import api as memory_api
    from astracore.modules.projects import api as projects_api

    monkeypatch.setattr(conversations_api, "_get_db_url", lambda: db_url)
    monkeypatch.setattr(memory_api, "_get_db_url", lambda: db_url)
    monkeypatch.setattr(projects_api, "_get_db_url", lambda: db_url)
    conversations_api._get_memory_engine.cache_clear()

    yield db_url

    conversations_api._get_memory_engine.cache_clear()
    get_engine.cache_clear()


async def test_memory_engine_formats_relevant_context(memory_db) -> None:
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.memory.application.engine import MemoryEngine
    from astracore.modules.memory.domain import MemoryScope, MemoryType

    session_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))
    project = await engine.create_project(
        name="AstraCoreAI",
        root_paths=["D:/project/study/AstraCoreAI"],
        description="AI framework",
    )
    await engine.bind_conversation(
        conversation_id=session_id,
        project_id=project.id,
        locked=True,
        source="manual",
    )
    await engine.create_memory(
        scope=MemoryScope.SESSION,
        memory_type=MemoryType.DECISION,
        content="Memory 系统采用混合 project 识别。",
        session_id=session_id,
        importance=5,
    )
    await engine.create_memory(
        scope=MemoryScope.PROJECT,
        memory_type=MemoryType.STATE,
        content="AstraCoreAI 使用 FastAPI Service 和 React SPA。",
        project_id=project.id,
        importance=4,
    )
    await engine.create_memory(
        scope=MemoryScope.USER,
        memory_type=MemoryType.PREFERENCE,
        content="用户偏好直接务实的工程回答。",
        importance=5,
    )

    context = await engine.build_memory_context(
        session_id=session_id,
        message="继续设计 Memory Engine",
    )

    assert "## Relevant Memory" in context
    assert "### Current Project State" in context
    assert "Memory 系统采用混合 project 识别。" in context
    assert "AstraCoreAI 使用 FastAPI Service 和 React SPA。" in context
    assert "用户偏好直接务实的工程回答。" in context


async def test_memory_api_crud_and_project_binding(memory_db) -> None:
    from types import SimpleNamespace

    from astracore.modules.memory import api as memory_api
    from astracore.modules.projects import api as projects_api

    mock_user = SimpleNamespace(id="default")
    conversation_id = uuid4()
    project = await projects_api.create_project(
        projects_api.ProjectCreate(
            name="StoryVault",
            root_paths=["D:/project/StoryVault"],
            description="小说项目",
        ),
        current_user=mock_user,
    )
    binding = await projects_api.bind_conversation_project(
        conversation_id,
        projects_api.ConversationProjectBind(project_id=project.id, locked=True, source="manual"),
        current_user=mock_user,
    )
    created = await memory_api.create_memory(
        memory_api.MemoryCreate(
            scope="project",
            type="state",
            content="卷一《观察期》已完结，卷二进入《贸易战》。",
            project_id=project.id,
            importance=5,
        ),
        current_user=mock_user,
    )

    page = await memory_api.list_memory(
        scope="project", project_id=project.id, current_user=mock_user
    )
    updated = await memory_api.update_memory(
        created.id,
        memory_api.MemoryUpdate(content="卷二《贸易战》聚焦贸易网络冲突。", locked=True),
        current_user=mock_user,
    )

    assert binding.project_id == project.id
    assert len(page.items) == 1
    assert page.items[0].content.startswith("卷一")
    assert updated.locked is True
    assert updated.content == "卷二《贸易战》聚焦贸易网络冲突。"

    await memory_api.delete_memory(created.id, current_user=mock_user)
    empty = await memory_api.list_memory(
        scope="project", project_id=project.id, current_user=mock_user
    )

    assert empty.total == 0


async def test_memory_extraction_requires_llm_decision(memory_db) -> None:
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.memory.application.engine import MemoryEngine

    session_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))

    memories = await engine.extract_and_store(
        session_id=session_id,
        user_message="记住：这个对话的目标是完成 Memory Engine 设计。",
        assistant_content="已记录。",
        source_run_id=str(uuid4()),
    )
    stored = await engine.list_memories(session_id=session_id)

    assert memories == []
    assert stored == []


async def test_llm_memory_extraction_creates_session_memory(memory_db) -> None:
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.memory.application.engine import MemoryEngine

    session_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))

    memories = await engine.extract_and_store(
        session_id=session_id,
        user_message="记录下当前工作目录",
        assistant_content="当前项目工作目录是 D:/project/study/AstraCoreAI",
        source_run_id=str(uuid4()),
        llm_adapter=_MemoryDecisionLLM(
            '{"should_remember": true, "scope": "session", "type": "fact", '
            '"subject": "工作目录", "content": "当前项目工作目录是 D:/project/study/AstraCoreAI", '
            '"summary": "项目工作目录", "importance": 4, "confidence": 0.95}'
        ),
        model="fake",
    )
    stored = await engine.list_memories(session_id=session_id)

    assert len(memories) == 1
    assert len(stored) == 1
    assert stored[0].content == "当前项目工作目录是 D:/project/study/AstraCoreAI"
    assert stored[0].subject == "工作目录"


async def test_memory_extraction_updates_existing_subject_instead_of_creating_duplicate(
    memory_db,
) -> None:
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.memory.application.engine import MemoryEngine
    from astracore.modules.memory.domain import MemoryScope, MemoryType

    session_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))
    await engine.create_memory(
        scope=MemoryScope.SESSION,
        memory_type=MemoryType.FACT,
        subject="工作目录",
        content="当前项目目录是 D:/project/study/AstraCoreAI",
        session_id=session_id,
        importance=3,
        confidence=0.7,
    )

    await engine.extract_and_store(
        session_id=session_id,
        user_message="确认一下工作目录",
        assistant_content="当前项目工作目录是 D:/project/study/AstraCoreAI",
        source_run_id=str(uuid4()),
        llm_adapter=_MemoryDecisionLLM(
            '{"should_remember": true, "scope": "session", "type": "fact", '
            '"subject": "工作目录", "content": "当前项目工作目录是 D:/project/study/AstraCoreAI", '
            '"importance": 4, "confidence": 0.9, "action": "update"}'
        ),
        model="fake",
    )

    stored = await engine.list_memories(session_id=session_id)

    assert len(stored) == 1
    assert stored[0].content == "当前项目工作目录是 D:/project/study/AstraCoreAI"
    assert stored[0].importance == 4
    assert stored[0].confidence == 0.9
    assert stored[0].use_count == 1


async def test_memory_extraction_does_not_overwrite_locked_conflict(memory_db) -> None:
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.memory.application.engine import MemoryEngine
    from astracore.modules.memory.domain import MemoryScope, MemoryStatus, MemoryType

    session_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))
    locked = await engine.create_memory(
        scope=MemoryScope.SESSION,
        memory_type=MemoryType.STATE,
        subject="阶段状态",
        content="当前阶段是方案讨论。",
        session_id=session_id,
        locked=True,
    )

    await engine.extract_and_store(
        session_id=session_id,
        user_message="阶段已经变了",
        assistant_content="当前阶段是直接实现。",
        source_run_id=str(uuid4()),
        llm_adapter=_MemoryDecisionLLM(
            '{"should_remember": true, "scope": "session", "type": "state", '
            '"subject": "阶段状态", "content": "当前阶段是直接实现。", '
            '"importance": 4, "confidence": 0.9}'
        ),
        model="fake",
    )

    active = await engine.list_memories(session_id=session_id)
    rejected = await engine.list_memories(session_id=session_id, status=MemoryStatus.REJECTED)

    assert len(active) == 1
    assert active[0].id == locked.id
    assert active[0].content == "当前阶段是方案讨论。"
    assert len(rejected) == 1
    assert rejected[0].metadata["decision"] == "conflict"
    assert rejected[0].metadata["conflicts_with"] == locked.id


async def test_session_memory_compaction_deletes_compressed_details(memory_db) -> None:
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.memory.application.engine import MemoryEngine
    from astracore.modules.memory.domain import MemoryScope, MemoryType

    session_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))
    for index in range(8):
        await engine.create_memory(
            scope=MemoryScope.SESSION,
            memory_type=MemoryType.FACT,
            subject=f"detail-{index}",
            content=f"阶段性细节 {index}",
            session_id=session_id,
        )

    summary = await engine.compact_session_memories(session_id=session_id, threshold=5)
    active = await engine.list_memories(session_id=session_id, limit=20)

    assert summary is not None
    assert len(active) <= 4
    assert summary.metadata["retention_action"] == "deleted"
    assert summary.metadata["compressed_from_count"] >= 4
    assert any(memory.id == summary.id for memory in active)


async def test_delete_conversation_cleans_related_memory_and_history(memory_db) -> None:
    from astracore.infrastructure.db.models import ChatRunRow, ChatSessionRow
    from astracore.infrastructure.db.session import get_session
    from astracore.infrastructure.memory.store import SQLMemoryStore
    from astracore.modules.chat import conversations_api
    from astracore.modules.memory.application.engine import MemoryEngine
    from astracore.modules.memory.domain import MemoryScope, MemoryType

    conversation_id = uuid4()
    engine = MemoryEngine(SQLMemoryStore(memory_db))
    memory = await engine.create_memory(
        scope=MemoryScope.SESSION,
        memory_type=MemoryType.FACT,
        subject="cleanup",
        content="这条记忆来自即将删除的对话。",
        session_id=conversation_id,
        conversation_id=conversation_id,
    )
    async with get_session(memory_db) as db:
        db.add(ChatSessionRow(session_id=str(conversation_id), messages=[]))
        db.add(
            ChatRunRow(
                id=str(uuid4()),
                session_id=str(conversation_id),
                status="done",
                request={},
                user_message="hi",
            )
        )
        await db.commit()

    mock_user = SimpleNamespace(id="default")
    await conversations_api.create_conversation(
        conversations_api.CreateConversationRequest(id=str(conversation_id), title="待删除"),
        current_user=mock_user,
    )
    await conversations_api.delete_conversation(conversation_id, current_user=mock_user)

    assert await engine.get_memory(memory.id) is None
    async with get_session(memory_db) as db:
        assert await db.get(ChatSessionRow, str(conversation_id)) is None


async def test_chat_pipeline_injects_memory_context() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from astracore.modules.chat.pipeline import ChatPipeline
    from astracore.modules.rag.application.pipeline import RAGPipeline
    from astracore.shared.policy.engine import PolicyEngine

    class _MemoryEngineStub:
        async def build_memory_context(self, *, session_id, message):
            return "## Relevant Memory\n\n### User Preferences\n- 用户偏好直接回答。"

    pipeline = ChatPipeline(
        config=SimpleNamespace(memory=SimpleNamespace(db_url="sqlite+aiosqlite:///:memory:")),
        memory=AsyncMock(),
        rag_pipeline=AsyncMock(spec=RAGPipeline),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
        memory_engine=_MemoryEngineStub(),
    )
    pipeline._get_setting = AsyncMock(return_value="")
    pipeline._load_all_skills = AsyncMock(return_value=[])

    system_prompt = await pipeline._build_system_prompt(
        session_id=uuid4(),
        enable_rag=False,
        message="你好",
    )

    assert system_prompt is not None
    assert "## Relevant Memory" in system_prompt
    assert "用户偏好直接回答。" in system_prompt
