"""ChromaDB retriever adapter — all sync calls wrapped in run_in_executor."""

import asyncio
from typing import Any

from astracore.core.domain.retrieval import Citation, RetrievalQuery, RetrievedChunk
from astracore.core.ports.retriever import IndexResult, RetrieverAdapter


class ChromaRetrieverAdapter(RetrieverAdapter):
    """ChromaDB vector store adapter.

    ChromaDB's Python client is synchronous. Every DB call is dispatched via
    asyncio.get_event_loop().run_in_executor so the event loop is not blocked.
    """

    def __init__(self, collection_name: str = "astracore", persist_directory: str | None = None):
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self._client: Any = None
        self._collection: Any = None

    def _get_client(self) -> Any:
        """Lazy load ChromaDB client."""
        if self._client is None:
            try:
                import chromadb

                if self.persist_directory:
                    self._client = chromadb.PersistentClient(path=self.persist_directory)
                else:
                    self._client = chromadb.Client()

                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            except ImportError as e:
                raise ImportError(
                    "chromadb not installed. Install with: pip install chromadb"
                ) from e
        return self._client

    def _chunk_text(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        """按 Markdown 结构分块：优先在标题/段落边界切割，超长段落再按句子细切。"""
        import re

        # 按 Markdown 标题或连续空行分割成语义段落
        sections = re.split(r"\n(?=#{1,6} )|\n{2,}", text)
        sections = [s.strip() for s in sections if s.strip()]

        chunks: list[str] = []
        current = ""

        for section in sections:
            # 当前段落本身就超长，需要细切
            if len(section) > chunk_size:
                # 先把已积累的内容存起来
                if current:
                    chunks.append(current.strip())
                    current = ""
                # 按句子边界（。！？\n）细切
                sentences = re.split(r"(?<=[。！？\n])", section)
                for sent in sentences:
                    if len(current) + len(sent) <= chunk_size:
                        current += sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        # 单句超长则强制截断
                        if len(sent) > chunk_size:
                            for i in range(0, len(sent), chunk_size - chunk_overlap):
                                chunks.append(sent[i:i + chunk_size].strip())
                        else:
                            current = sent
                continue

            # 加入当前段落不超长，则合并
            if len(current) + len(section) + 2 <= chunk_size:
                current = (current + "\n\n" + section).strip() if current else section
            else:
                if current:
                    chunks.append(current.strip())
                current = section

        if current:
            chunks.append(current.strip())

        return [c for c in chunks if c]

    async def index_document(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
    ) -> IndexResult:
        """Index a document into ChromaDB (non-blocking)."""
        try:
            self._get_client()

            chunks = self._chunk_text(text, chunk_size, chunk_overlap)
            chunk_ids = [f"{document_id}_{i}" for i in range(len(chunks))]
            chunk_metadata = [
                {"document_id": document_id, "chunk_index": i, **(metadata or {})}
                for i in range(len(chunks))
            ]

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._collection.upsert(
                    documents=chunks,
                    ids=chunk_ids,
                    metadatas=chunk_metadata,
                ),
            )

            return IndexResult(
                document_id=document_id,
                chunks_indexed=len(chunks),
                success=True,
            )

        except Exception as e:
            return IndexResult(
                document_id=document_id,
                chunks_indexed=0,
                success=False,
                error=str(e),
            )

    async def retrieve(
        self,
        query: RetrievalQuery,
    ) -> list[RetrievedChunk]:
        """Retrieve relevant chunks from ChromaDB (non-blocking)."""
        self._get_client()

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: self._collection.query(
                query_texts=[query.text],
                n_results=query.top_k,
                where=query.filters if query.filters else None,
            ),
        )

        chunks: list[RetrievedChunk] = []

        if results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            distances = (
                results["distances"][0] if results["distances"] else [0.0] * len(docs)
            )
            metas = (
                results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
            )

            for doc, distance, meta in zip(docs, distances, metas, strict=False):
                score = 1.0 - distance if distance else 1.0

                if score >= query.min_score:
                    chunks.append(
                        RetrievedChunk(
                            content=doc,
                            score=score,
                            citation=Citation(
                                source_id=meta.get("document_id", "unknown"),
                                source_type="document",
                                title=meta.get("title"),
                                metadata=meta,
                            ),
                            metadata=meta,
                        )
                    )

        return chunks

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """Sort by score descending.

        Note: this is not semantic reranking — it is a simple score sort.
        For production, replace with a cross-encoder reranker.
        """
        return sorted(chunks, key=lambda x: x.score, reverse=True)[:top_k]

    async def delete_document(
        self,
        document_id: str,
    ) -> bool:
        """Delete a document from ChromaDB (non-blocking)."""
        try:
            self._get_client()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._collection.delete(where={"document_id": document_id}),
            )
            return True
        except Exception:
            return False
