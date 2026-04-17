"""启动时扫描 docs/ 目录，将所有 .md 文件写入向量数据库。

新增文档只需在 docs/ 目录下放一个 .md 文件即可，文件名作为 document_id。
支持可选的 frontmatter（`--- title: xxx ---`），用于设置文档标题元数据。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from astracore.core.application.rag import RAGPipeline

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).parent / "docs"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title\s*:\s*(.+)$", re.MULTILINE)


def _parse_md(path: Path) -> tuple[str, str, str]:
    """解析 .md 文件，返回 (document_id, title, content)。"""
    raw = path.read_text(encoding="utf-8")
    document_id = path.stem
    title = document_id
    content = raw

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        frontmatter = fm_match.group(1)
        title_match = _TITLE_RE.search(frontmatter)
        if title_match:
            title = title_match.group(1).strip()
        content = raw[fm_match.end():]

    return document_id, title, content.strip()


async def seed_documents(pipeline: RAGPipeline) -> None:
    """扫描 docs/ 目录，将所有 .md 文件写入向量数据库。幂等可重复执行。"""
    if not DOCS_DIR.exists():
        logger.warning("docs 目录不存在: %s，跳过种子写入", DOCS_DIR)
        return

    md_files = sorted(DOCS_DIR.glob("*.md"))
    if not md_files:
        logger.info("docs 目录为空，无种子文档可写入")
        return

    success_count = 0
    for path in md_files:
        document_id, title, content = _parse_md(path)
        result = await pipeline.index_document(
            document_id=document_id,
            text=content,
            metadata={"title": title, "source": "seed"},
        )
        if result:
            success_count += 1
            logger.debug("种子文档写入成功: %s (%s)", document_id, title)
        else:
            logger.warning("种子文档写入失败: %s", document_id)

    logger.info("种子文档写入完成: %d/%d 成功", success_count, len(md_files))
