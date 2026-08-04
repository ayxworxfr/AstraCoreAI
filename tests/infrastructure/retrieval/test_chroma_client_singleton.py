"""回归：同一 persist 目录并发打开 PersistentClient 不得竞态失败。"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from astracore.infrastructure.memory.vector import MemoryVectorAdapter
from astracore.infrastructure.retrieval.chroma import ChromaRetrieverAdapter
from astracore.infrastructure.retrieval.chroma_client import get_chroma_client, reset_chroma_clients


@pytest.fixture
def persist_dir() -> Path:
    root = Path(tempfile.mkdtemp(prefix="astracore-chroma-"))
    reset_chroma_clients()
    yield root
    reset_chroma_clients()
    shutil.rmtree(root, ignore_errors=True)


def test_concurrent_get_chroma_client_same_path(persist_dir: Path) -> None:
    path = str(persist_dir)

    def open_one(_: int) -> object:
        return get_chroma_client(path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(open_one, i) for i in range(8)]
        clients = [f.result() for f in as_completed(futs)]

    assert all(c is clients[0] for c in clients)
    clients[0].get_or_create_collection(name="smoke_collection")


def test_memory_and_rag_adapters_concurrent_init(persist_dir: Path) -> None:
    path = str(persist_dir)
    memory = MemoryVectorAdapter(persist_directory=path)
    rag = ChromaRetrieverAdapter(collection_name="astracore", persist_directory=path)

    async def run() -> None:
        await asyncio.gather(
            memory._ensure_init(),
            asyncio.to_thread(rag._get_client),
            memory._ensure_init(),
            asyncio.to_thread(rag._get_client),
        )

    asyncio.run(run())

    assert memory._available is True
    assert memory._client is rag._client
    assert rag._collection is not None


def test_concurrent_memory_adapter_instances(persist_dir: Path) -> None:
    path = str(persist_dir)
    adapters = [MemoryVectorAdapter(persist_directory=path) for _ in range(4)]

    async def run() -> None:
        await asyncio.gather(*(a._ensure_init() for a in adapters))

    asyncio.run(run())

    assert all(a._available is True for a in adapters)
    assert all(a._client is adapters[0]._client for a in adapters)
