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


async def test_retrieve_with_citations_calls_rerank_with_top_k(rag, mock_retriever):
    chunk = _chunk("data")
    mock_retriever.retrieve.return_value = [chunk]
    mock_retriever.rerank.return_value = [chunk]

    result = await rag.retrieve_with_citations("q", top_k=3)

    assert result == [chunk]
    mock_retriever.rerank.assert_called_once_with("q", [chunk], top_k=3)
