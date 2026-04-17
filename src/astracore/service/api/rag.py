"""RAG API endpoints."""

import os
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from astracore.adapters.retrieval.chroma import ChromaRetrieverAdapter
from astracore.core.application.rag import RAGPipeline

router = APIRouter()


class IndexRequest(BaseModel):
    """Document indexing request."""

    document_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class IndexResponse(BaseModel):
    """Document indexing response."""

    document_id: str
    success: bool
    message: str


class RetrievalRequest(BaseModel):
    """Retrieval request."""

    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)


class RetrievalResponse(BaseModel):
    """Retrieval response."""

    chunks: list[dict[str, Any]]
    count: int


@lru_cache(maxsize=1)
def _get_rag_pipeline() -> RAGPipeline:
    """Get RAG pipeline instance (cached — avoids creating a new ChromaDB connection per request)."""
    collection_name = os.getenv("ASTRACORE__RETRIEVAL__COLLECTION_NAME", "astracore")
    persist_directory = os.getenv("ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY", "./chroma_db")
    retriever = ChromaRetrieverAdapter(
        collection_name=collection_name,
        persist_directory=persist_directory,
    )
    return RAGPipeline(retriever=retriever)


@router.post("/index", response_model=IndexResponse)
async def index_document(request: IndexRequest) -> IndexResponse:
    """Index a document."""
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
    """Retrieve relevant chunks."""
    try:
        pipeline = _get_rag_pipeline()

        chunks = await pipeline.retrieve_with_citations(
            query=request.query,
            top_k=request.top_k,
            min_score=request.min_score,
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

        return RetrievalResponse(
            chunks=chunks_data,
            count=len(chunks_data),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{document_id}")
async def delete_document(document_id: str) -> dict[str, Any]:
    """Delete a document."""
    try:
        pipeline = _get_rag_pipeline()
        success = await pipeline.delete_document(document_id)

        return {
            "document_id": document_id,
            "deleted": success,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
