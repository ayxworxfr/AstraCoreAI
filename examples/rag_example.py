"""RAG example using AstraCore SDK."""

import asyncio
import os

from astracore.sdk import AstraCoreClient, AstraCoreConfig
from astracore.sdk.config import LLMConfig, RetrievalConfig


async def main() -> None:
    """Run RAG example."""
    config = AstraCoreConfig(
        llm=LLMConfig(
            provider="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY", "test-key"),
        ),
        retrieval=RetrievalConfig(
            collection_name="docs",
            persist_directory="./chroma_db",
        ),
    )

    client = AstraCoreClient(config)

    print("=== RAG Example ===\n")

    documents = [
        {
            "id": "doc1",
            "text": "AstraCore AI is an enterprise-grade Python AI framework "
            "built with Clean Architecture principles.",
        },
        {
            "id": "doc2",
            "text": "The framework supports multiple LLM providers including "
            "Anthropic Claude and OpenAI GPT models.",
        },
        {
            "id": "doc3",
            "text": "AstraCore provides built-in RAG capabilities with vector "
            "search and document indexing.",
        },
    ]

    print("Indexing documents...\n")
    for doc in documents:
        success = await client.index_document(
            document_id=doc["id"],
            text=doc["text"],
            metadata={"source": "example"},
        )
        print(f"Indexed {doc['id']}: {success}")

    print("\n=== Retrieving relevant information ===\n")

    query = "What LLM providers are supported?"
    chunks = await client.retrieve(query=query, top_k=3)

    print(f"Query: {query}\n")
    for i, chunk in enumerate(chunks, 1):
        print(f"{i}. Score: {chunk.score:.2f}")
        print(f"   Content: {chunk.content}\n")


if __name__ == "__main__":
    asyncio.run(main())
