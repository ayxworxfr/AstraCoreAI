"""RAG pipeline implementation."""

from typing import Any

from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.retrieval import RetrievalQuery, RetrievedChunk
from astracore.core.ports.retriever import RetrieverAdapter


class RAGPipeline:
    """Retrieval-Augmented Generation pipeline."""

    def __init__(self, retriever: RetrieverAdapter):
        self.retriever = retriever

    async def index_document(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Index a document for retrieval."""
        result = await self.retriever.index_document(
            document_id=document_id,
            text=text,
            metadata=metadata,
        )
        return result.success

    async def retrieve_and_inject(
        self,
        query: str,
        messages: list[Message],
        top_k: int = 5,
        min_score: float = 0.7,
    ) -> list[Message]:
        """Retrieve relevant chunks and inject into messages."""
        retrieval_query = RetrievalQuery(
            text=query,
            top_k=top_k,
            min_score=min_score,
        )

        chunks = await self.retriever.retrieve(retrieval_query)

        if not chunks:
            return messages

        reranked = await self.retriever.rerank(query, chunks, top_k=top_k)

        context_parts = []
        for chunk in reranked:
            citation = chunk.citation
            context_parts.append(
                f"[{citation.source_id}]: {chunk.content}\n"
                f"(Source: {citation.title or citation.source_id}, Score: {chunk.score:.2f})"
            )

        context_message = Message(
            role=MessageRole.SYSTEM,
            content=(
                "Below is relevant context retrieved from the knowledge base:\n\n"
                + "\n\n".join(context_parts)
                + "\n\nUse this context to answer the user's question. "
                "Include citations when referencing information from the context."
            ),
        )

        return [context_message] + messages

    async def retrieve_with_citations(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        """Retrieve chunks with citations."""
        retrieval_query = RetrievalQuery(
            text=query,
            top_k=top_k,
            min_score=min_score,
        )

        chunks = await self.retriever.retrieve(retrieval_query)
        return await self.retriever.rerank(query, chunks, top_k=top_k)

    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from the index."""
        return await self.retriever.delete_document(document_id)
