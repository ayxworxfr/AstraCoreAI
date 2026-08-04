"""进程内 Chroma PersistentClient 单例。

chromadb 1.5+ 默认 Rust 后端在同一 persist 目录上并发首次打开会竞态失败
（`RustBindingsAPI` 无 `bindings` / `default_tenant`）。所有适配器必须经此获取 client。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_clients: dict[str, Any] = {}


def _client_key(persist_directory: str) -> str:
    return str(Path(persist_directory).expanduser().resolve())


def get_chroma_client(persist_directory: str | None = None) -> Any:
    """返回指定路径的进程级 chromadb client（持久化路径串行创建并复用）。"""
    import chromadb

    if not persist_directory:
        # 内存 client 无磁盘竞态，不缓存（避免测试互相污染）
        return chromadb.Client()

    key = _client_key(persist_directory)
    with _lock:
        existing = _clients.get(key)
        if existing is not None:
            return existing
        Path(persist_directory).expanduser().mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=persist_directory)
        _clients[key] = client
        return client


def reset_chroma_clients() -> None:
    """测试用：清空 client 缓存（不删除磁盘数据）。"""
    with _lock:
        _clients.clear()
