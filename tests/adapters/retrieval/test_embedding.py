"""Tests for Chroma embedding function selection."""

import pytest

from astracore.infrastructure.retrieval.embedding import build_chroma_embedding_function


def test_default_embedding_model_uses_chroma_onnx() -> None:
    embedding_function = build_chroma_embedding_function("all-MiniLM-L6-v2")

    assert embedding_function.__class__.__name__ == "ONNXMiniLM_L6_V2"


def test_non_default_embedding_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="Only all-MiniLM-L6-v2"):
        build_chroma_embedding_function("paraphrase-multilingual-MiniLM-L12-v2")
