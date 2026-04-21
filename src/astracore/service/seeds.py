"""启动时扫描 docs/ 目录，将所有 .md 文件写入向量数据库。

新增文档只需在 docs/ 目录下放一个 .md 文件即可，文件名作为 document_id。
支持可选的 frontmatter（`--- title: xxx ---`），用于设置文档标题元数据。
"""

from __future__ import annotations

import re
from pathlib import Path

from astracore.core.application.rag import RAGPipeline
from astracore.runtime.observability.logger import get_logger

logger = get_logger(__name__)

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


_BUILTIN_SKILLS = [
    {
        "name": "通用助手",
        "description": "默认模式，平衡各类任务",
        "system_prompt": "你是一个有帮助、准确、诚实的 AI 助手。",
    },
    {
        "name": "代码助手",
        "description": "专注编程，优先提供代码示例",
        "system_prompt": (
            "你是一名专业的软件工程师。回答编程问题时优先给出可运行的代码示例，"
            "并简要解释关键逻辑。如果问题不涉及编程，礼貌说明并尝试帮助。"
        ),
    },
    {
        "name": "写作助手",
        "description": "文章写作、润色、改写",
        "system_prompt": (
            "你是一名专业写作助手。帮助用户撰写、润色和改写文章。"
            "注重语言表达的准确性和可读性，保持用户原有的语气和风格。"
        ),
    },
    {
        "name": "翻译官",
        "description": "中英文互译，保持原意",
        "system_prompt": (
            "你是一名专业翻译。在中文和英文之间进行高质量互译，"
            "忠实于原文含义，同时保证译文自然流畅。直接给出译文，不加额外解释。"
        ),
    },
    {
        "name": "数据分析师",
        "description": "数据分析、统计解读",
        "system_prompt": (
            "你是一名数据分析专家。帮助用户理解数据、解读统计结果、设计分析方案。"
            "回答时注重逻辑严谨，必要时说明数据局限性和分析假设。"
        ),
    },
]


async def seed_builtin_skills(db_url: str) -> None:
    """Insert built-in skills if they don't already exist. Idempotent."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from astracore.adapters.db.models import SkillRow
    from astracore.adapters.db.session import get_session

    async with get_session(db_url) as db:
        result = await db.execute(select(SkillRow).where(SkillRow.is_builtin == True))  # noqa: E712
        existing_names = {row.name for row in result.scalars().all()}

        for skill in _BUILTIN_SKILLS:
            if skill["name"] in existing_names:
                continue
            now = datetime.now(UTC)
            db.add(
                SkillRow(
                    id=str(uuid4()),
                    name=skill["name"],
                    description=skill["description"],
                    system_prompt=skill["system_prompt"],
                    is_builtin=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.commit()

    logger.info("内置 Skill 种子写入完成")
