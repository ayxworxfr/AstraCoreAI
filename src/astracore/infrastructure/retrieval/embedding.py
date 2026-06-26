"""Chroma embedding function factory."""

from __future__ import annotations

from typing import Any


def build_chroma_embedding_function(model_name: str = "all-MiniLM-L6-v2") -> Any:
    """Build the Chroma embedding function used by RAG and memory retrieval.

    Uses ChromaDB's built-in ONNXMiniLM_L6_V2 (no extra package required).
    ``model_name`` is accepted for API compatibility but only all-MiniLM-L6-v2
    is produced — callers that change the model must supply their own EF.
    """
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    return ONNXMiniLM_L6_V2()
