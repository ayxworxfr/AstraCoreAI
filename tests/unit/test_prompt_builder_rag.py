"""Tests for RAG gating in SystemPromptBuilder."""

from unittest.mock import AsyncMock

import pytest

from astracore.modules.chat.application.prompt_builder import (
    SystemPromptBuilder,
    should_skip_rag_query,
)
from astracore.modules.rag.domain import Citation, RetrievedChunk
from astracore.sdk.config import (
    AstraCoreConfig,
    LLMConfig,
    LLMProfileConfig,
    StorageConfig,
    VectorConfig,
)


@pytest.mark.parametrize(
    "query",
    [
        "你是谁",
        "你是什么",
        "你好",
        "Hello",
        "你能做什么",
        "你能做什么？",
        "介绍一下你自己",
        "你有什么功能",
        "嗯",
    ],
)
def test_should_skip_rag_query_meta_and_greetings(query: str) -> None:
    assert should_skip_rag_query(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "AstraCoreAI 的 DAG 工作流怎么设计",
        "记忆系统的 Tier-2 注入机制是什么",
        "帮我查一下 FastAPI 中间件写法",
    ],
)
def test_should_not_skip_rag_query_substantive(query: str) -> None:
    assert should_skip_rag_query(query) is False


def _make_builder(
    *,
    rag_min_score: float = 0.5,
    rag_pipeline: AsyncMock | None = None,
) -> SystemPromptBuilder:
    config = AstraCoreConfig(
        llm=LLMConfig(
            default_profile="test",
            profiles=[
                LLMProfileConfig(
                    id="test",
                    protocol="openai",
                    api_key="test-key",
                    model="gpt-4o",
                )
            ],
        ),
        storage=StorageConfig(
            vector=VectorConfig(rag_min_score=rag_min_score),
        ),
    )
    return SystemPromptBuilder(
        config=config,
        rag_pipeline=rag_pipeline or AsyncMock(),
    )


async def test_knowledge_layer_skips_meta_query_without_retrieval() -> None:
    rag = AsyncMock()
    builder = _make_builder(rag_pipeline=rag)

    result = await builder.retrieve_rag_context("你是谁", "user-1")

    assert result == ""
    rag.retrieve_with_citations.assert_not_called()


async def test_knowledge_layer_passes_min_score_to_retriever() -> None:
    rag = AsyncMock()
    rag.retrieve_with_citations.return_value = []
    builder = _make_builder(rag_pipeline=rag, rag_min_score=0.55)
    # 避免依赖真实 DB；CI 无库时 _get_setting 失败会被吞掉，导致检索根本不执行
    builder._get_setting = AsyncMock(return_value="4")  # type: ignore[method-assign]

    await builder.retrieve_rag_context("DAG 工作流状态怎么存", "user-1")

    rag.retrieve_with_citations.assert_awaited_once_with(
        query="DAG 工作流状态怎么存",
        top_k=4,
        min_score=0.55,
    )


async def test_knowledge_layer_returns_empty_when_no_chunks() -> None:
    rag = AsyncMock()
    rag.retrieve_with_citations.return_value = []
    builder = _make_builder(rag_pipeline=rag)
    builder._get_setting = AsyncMock(return_value="4")  # type: ignore[method-assign]

    result = await builder.retrieve_rag_context("记忆系统怎么压缩", "user-1")

    assert result == ""


async def test_knowledge_layer_still_retrieves_when_settings_db_fails() -> None:
    rag = AsyncMock()
    rag.retrieve_with_citations.return_value = []
    builder = _make_builder(rag_pipeline=rag, rag_min_score=0.55)
    builder._get_setting = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]

    await builder.retrieve_rag_context("DAG 工作流状态怎么存", "user-1")

    rag.retrieve_with_citations.assert_awaited_once_with(
        query="DAG 工作流状态怎么存",
        top_k=4,
        min_score=0.55,
    )


async def test_knowledge_layer_wraps_retrieved_chunks() -> None:
    chunk = RetrievedChunk(
        content="workflow state lives in WorkflowState",
        score=0.82,
        citation=Citation(source_id="doc-1", source_type="document", title="DAG 引擎"),
    )
    rag = AsyncMock()
    rag.retrieve_with_citations.return_value = [chunk]
    builder = _make_builder(rag_pipeline=rag)
    builder._get_setting = AsyncMock(return_value="4")  # type: ignore[method-assign]

    result = await builder.retrieve_rag_context("WorkflowState 存在哪", "user-1")

    assert "<knowledge>" in result
    assert "DAG 引擎" in result
    assert 'source="rag"' in result
