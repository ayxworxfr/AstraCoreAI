"""Retrieval and RAG domain models."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    """Query for retrieval system."""

    query_id: UUID = Field(default_factory=uuid4)
    text: str
    top_k: int = 5
    min_score: float = 0.0
    filters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Citation(BaseModel):
    """Source citation for retrieved content."""

    source_id: str
    source_type: str
    title: str | None = None
    url: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    """Retrieved chunk from vector store."""

    chunk_id: UUID = Field(default_factory=uuid4)
    content: str
    score: float
    citation: Citation
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def token_estimate(self) -> int:
        """Rough token estimation."""
        return len(self.content) // 4
