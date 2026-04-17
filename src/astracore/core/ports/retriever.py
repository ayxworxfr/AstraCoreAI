"""Retriever adapter port interface."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.core.domain.retrieval import RetrievalQuery, RetrievedChunk


class IndexResult(BaseModel):
    """Result of indexing operation."""

    result_id: UUID = Field(default_factory=uuid4)
    document_id: str
    chunks_indexed: int
    success: bool
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RetrieverAdapter(ABC):
    """Abstract retriever adapter interface."""

    @abstractmethod
    async def index_document(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
    ) -> IndexResult:
        """Index a document into the vector store."""
        pass

    @abstractmethod
    async def retrieve(
        self,
        query: RetrievalQuery,
    ) -> list[RetrievedChunk]:
        """Retrieve relevant chunks."""
        pass

    @abstractmethod
    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """Rerank retrieved chunks."""
        pass

    @abstractmethod
    async def delete_document(
        self,
        document_id: str,
    ) -> bool:
        """Delete a document from the index."""
        pass
