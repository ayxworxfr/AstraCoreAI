"""Fast test DB setup via schema template clone.

Windows 上对空文件 SQLite 跑 ``Base.metadata.create_all`` 要数秒；
进程内建一次 schema 模板，之后每测 ``shutil.copy2``（毫秒级）即可。
不改生产 ``get_engine`` / ``init_db`` 语义。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from astracore.infrastructure.db.session import get_engine, init_db

_TEMPLATE_PATH: Path | None = None


async def _ensure_schema_template() -> Path:
    global _TEMPLATE_PATH
    if _TEMPLATE_PATH is not None:
        return _TEMPLATE_PATH

    template_dir = Path(tempfile.mkdtemp(prefix="astracore_schema_"))
    path = template_dir / "template.db"
    url = f"sqlite+aiosqlite:///{path}"

    get_engine.cache_clear()
    await init_db(url)
    engine = get_engine(url)
    await engine.dispose()
    get_engine.cache_clear()

    _TEMPLATE_PATH = path
    return path


async def prepare_test_db(tmp_path: Path, name: str = "test.db") -> str:
    """Clone the shared schema template into ``tmp_path`` and return a db URL."""
    template = await _ensure_schema_template()
    dest = tmp_path / name
    shutil.copy2(template, dest)
    get_engine.cache_clear()
    return f"sqlite+aiosqlite:///{dest}"
