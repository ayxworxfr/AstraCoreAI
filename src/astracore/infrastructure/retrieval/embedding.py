"""Chroma embedding function factory."""

from __future__ import annotations

from typing import Any

_ONNX_MODEL_NAME = "all-MiniLM-L6-v2"


def build_chroma_embedding_function(model_name: str = _ONNX_MODEL_NAME) -> Any:
    """Build the Chroma embedding function used by RAG and memory retrieval."""
    if model_name != _ONNX_MODEL_NAME:
        raise ValueError("Only all-MiniLM-L6-v2 is supported by the default ONNX embedding backend")

    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    return ONNXMiniLM_L6_V2()
