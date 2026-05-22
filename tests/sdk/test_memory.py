"""Tests for SDK structured memory and project facades."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from astracore.infrastructure.db.session import get_engine
from astracore.modules.memory.domain import MemoryScope, MemoryStatus, MemoryType
from astracore.sdk.client import AstraCoreClient
from astracore.sdk.config import AstraCoreConfig, LLMConfig, LLMProfileConfig, MemoryConfig


@pytest.fixture
async def sdk_client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sdk-memory.db'}"
    get_engine.cache_clear()
    config = AstraCoreConfig(
        llm=LLMConfig(
            default_profile="test",
            profiles=[
                LLMProfileConfig(
                    id="test",
                    protocol="openai",
                    api_key="test-key",
                    base_url="https://api.deepseek.com",
                    model="deepseek-v4-flash",
                )
            ],
        ),
        memory=MemoryConfig(redis_url="redis://localhost:6379/0", db_url=db_url),
    )
    async with AstraCoreClient(config=config) as client:
        yield client
    get_engine.cache_clear()


async def test_sdk_memory_crud(sdk_client: AstraCoreClient) -> None:
    session_id = uuid4()

    created = await sdk_client.memory.create(
        scope="session",
        memory_type="fact",
        subject="工作目录",
        content="当前项目目录是 D:/project/study/AstraCoreAI",
        session_id=session_id,
        importance=4,
    )
    updated = await sdk_client.memory.update(
        created.id,
        content="当前项目工作目录是 D:/project/study/AstraCoreAI",
        status=MemoryStatus.ACTIVE,
        confidence=0.9,
    )
    listed = await sdk_client.memory.list(
        scope=MemoryScope.SESSION,
        memory_type=MemoryType.FACT,
        session_id=session_id,
    )

    assert updated.content == "当前项目工作目录是 D:/project/study/AstraCoreAI"
    assert updated.confidence == 0.9
    assert [memory.id for memory in listed] == [created.id]

    await sdk_client.memory.delete(created.id)
    assert await sdk_client.memory.get(created.id) is None


async def test_sdk_memory_update_preserves_unset_fields(sdk_client: AstraCoreClient) -> None:
    session_id = uuid4()
    created = await sdk_client.memory.create(
        scope="session",
        memory_type="fact",
        subject="原始标题",
        content="原始内容",
        session_id=session_id,
        importance=3,
    )

    # Only update content — other fields should stay the same.
    updated = await sdk_client.memory.update(created.id, content="更新后的内容")

    assert updated.content == "更新后的内容"
    assert updated.subject == "原始标题"
    assert updated.importance == 3


async def test_sdk_memory_delete_session(sdk_client: AstraCoreClient) -> None:
    session_id = uuid4()
    await sdk_client.memory.create(
        scope="session",
        memory_type="state",
        subject="阶段",
        content="当前阶段是 SDK 适配。",
        session_id=session_id,
    )

    deleted = await sdk_client.memory.delete_session(session_id)

    assert deleted == 1
    assert await sdk_client.memory.list(scope="session", session_id=session_id) == []


async def test_sdk_memory_delete_conversation(sdk_client: AstraCoreClient) -> None:
    conversation_id = uuid4()
    # Create a session-scoped memory with this session/conversation id.
    await sdk_client.memory.create(
        scope="session",
        memory_type="fact",
        subject="会话事实",
        content="内容",
        session_id=conversation_id,
    )
    # Create a project-scoped memory linked to the same conversation_id.
    await sdk_client.memory.create(
        scope="project",
        memory_type="decision",
        subject="项目决策",
        content="内容",
        conversation_id=conversation_id,
    )

    deleted = await sdk_client.memory.delete_conversation(conversation_id)

    assert deleted >= 2
    assert await sdk_client.memory.list(scope="session", session_id=conversation_id) == []
    assert await sdk_client.memory.list(scope="project", memory_type=MemoryType.DECISION) == []


async def test_sdk_projects_and_conversation_binding(sdk_client: AstraCoreClient) -> None:
    conversation_id = uuid4()
    project = await sdk_client.projects.create(
        name="AstraCoreAI",
        root_paths=["D:/project/study/AstraCoreAI"],
        description="AI framework",
    )

    binding = await sdk_client.projects.bind_conversation(
        conversation_id=conversation_id,
        project_id=project.id,
        locked=True,
    )
    conversation = sdk_client.conversation(session_id=conversation_id)
    rebound = await conversation.bind_project(project.id, locked=True)

    assert binding.project_id == project.id
    assert rebound.project_id == project.id
    assert rebound.locked is True
    assert (await sdk_client.projects.get_conversation_binding(conversation_id)) is not None


async def test_sdk_projects_list(sdk_client: AstraCoreClient) -> None:
    await sdk_client.projects.create(name="Project A")
    await sdk_client.projects.create(name="Project B")

    projects = await sdk_client.projects.list()

    names = {p.name for p in projects}
    assert {"Project A", "Project B"}.issubset(names)


async def test_sdk_clear_session_removes_all_memories(sdk_client: AstraCoreClient) -> None:
    session_id = uuid4()

    # Session-scoped memory (by session_id).
    await sdk_client.memory.create(
        scope="session",
        memory_type="state",
        subject="阶段",
        content="当前阶段是 SDK 适配。",
        session_id=session_id,
    )
    # A memory linked via conversation_id (may be a different scope).
    await sdk_client.memory.create(
        scope="project",
        memory_type="fact",
        subject="项目事实",
        content="内容",
        conversation_id=session_id,
    )

    await sdk_client.clear_session(session_id)

    assert await sdk_client.memory.list(scope="session", session_id=session_id) == []
    assert await sdk_client.memory.list(scope="project", memory_type=MemoryType.FACT) == []


async def test_sdk_chat_triggers_memory_extraction(sdk_client: AstraCoreClient) -> None:
    """Verify that chat() calls extract_and_store on the memory engine."""
    with patch.object(
        sdk_client._memory_engine, "extract_and_store", new_callable=AsyncMock
    ) as mock_extract:
        mock_extract.return_value = []

        with (
            patch.object(sdk_client._pipeline, "prepare", new_callable=AsyncMock) as mock_prepare,
            patch.object(sdk_client._pipeline, "execute", new_callable=AsyncMock) as mock_execute,
        ):
            from astracore.modules.chat.domain.chat_context import ChatContext
            from astracore.sdk.config import LLMProfileConfig

            profile = LLMProfileConfig(
                id="test",
                protocol="openai",
                api_key="key",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
            )
            fake_ctx = ChatContext(
                session_id=uuid4(),
                message="测试消息",
                profile=profile,
                temperature=0.7,
                system_prompt=None,
                context_max_messages=20,
                mode="normal",
                llm_kwargs={},
                tool_adapter=None,  # type: ignore[arg-type]
                allowed_tools=frozenset(),
                anchor_skill=None,
                routed_skills=(),
                skill_has_refs=False,
                anchor_id=None,
            )
            mock_prepare.return_value = fake_ctx
            mock_execute.return_value = "模拟回复"

            await sdk_client.chat("测试消息")

        mock_extract.assert_awaited_once()
        call_kwargs = mock_extract.call_args.kwargs
        assert call_kwargs["user_message"] == "测试消息"
        assert call_kwargs["assistant_content"] == "模拟回复"
