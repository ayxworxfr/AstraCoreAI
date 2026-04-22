"""启动时执行数据初始化：向量库文档写入 + 内置 Skill 写入。

- docs/    目录下的 .md 文件写入向量数据库，新增文档只需放文件即可
- skills/  目录下的 .md 文件作为内置 Skill 写入 SQLite，按文件名排序
  - frontmatter 字段: name（必填）、description、default（true 表示首次启动时设为默认 Skill）
  - 文件正文作为 system_prompt
"""

from __future__ import annotations

import re
from pathlib import Path

from astracore.runtime.observability.logger import get_logger

logger = get_logger(__name__)

DOCS_DIR = Path(__file__).parent / "docs"
SKILLS_DIR = Path(__file__).parent / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title\s*:\s*(.+)$", re.MULTILINE)
_KEY_VALUE_RE = re.compile(r"^(\w+)\s*:\s*(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# 文档种子（向量库）
# ---------------------------------------------------------------------------


def _parse_doc_md(path: Path) -> tuple[str, str, str]:
    """解析文档 .md，返回 (document_id, title, content)。"""
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


async def seed_documents(pipeline: object) -> None:
    """扫描 docs/ 目录，将所有 .md 文件写入向量数据库。幂等可重复执行。"""
    from astracore.core.application.rag import RAGPipeline

    assert isinstance(pipeline, RAGPipeline)

    if not DOCS_DIR.exists():
        logger.warning("docs 目录不存在: %s，跳过种子写入", DOCS_DIR)
        return

    md_files = sorted(DOCS_DIR.glob("*.md"))
    if not md_files:
        logger.info("docs 目录为空，无种子文档可写入")
        return

    success_count = 0
    for path in md_files:
        document_id, title, content = _parse_doc_md(path)
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


# ---------------------------------------------------------------------------
# Skill 种子（SQLite）
# ---------------------------------------------------------------------------


def _parse_skill_md(path: Path) -> dict[str, str]:
    """解析 skill .md，返回包含 source_key / name / description / system_prompt / default 的 dict。

    frontmatter 支持字段：
      name        必填，Skill 显示名称
      description 选填，简短描述
      default     选填，true 表示首次启动时自动设为默认 Skill
    文件正文作为 system_prompt。source_key 取文件名（不含扩展名），作为稳定标识符。
    """
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    system_prompt = raw.strip()

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        for m in _KEY_VALUE_RE.finditer(fm_match.group(1)):
            meta[m.group(1).strip().lower()] = m.group(2).strip()
        system_prompt = raw[fm_match.end():].strip()

    if not meta.get("name"):
        raise ValueError(f"Skill 文件缺少 name 字段: {path}")

    return {
        "source_key": path.stem,
        "name": meta["name"],
        "description": meta.get("description", ""),
        "system_prompt": system_prompt,
        "default": meta.get("default", "false").lower() == "true",
    }


def _load_builtin_skills() -> list[dict[str, str]]:
    """按文件名排序加载 skills/ 目录下所有 .md 文件。"""
    if not SKILLS_DIR.exists():
        logger.warning("skills 目录不存在: %s，跳过内置 Skill 加载", SKILLS_DIR)
        return []

    skills = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            skills.append(_parse_skill_md(path))
        except Exception:
            logger.exception("解析 Skill 文件失败: %s", path)

    return skills


async def _ensure_source_key_column(db_url: str) -> None:
    """为旧数据库补加 source_key 列（SQLite ALTER TABLE 幂等迁移）。"""
    from sqlalchemy import text

    from astracore.adapters.db.session import get_engine

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE skills ADD COLUMN source_key TEXT"))
            logger.info("已为 skills 表添加 source_key 列")
        except Exception:
            pass  # 列已存在，忽略


async def seed_builtin_skills(db_url: str) -> None:
    """写入并同步内置 Skill，首次启动时设置默认 Skill。

    - 匹配键：source_key（MD 文件名），与 Skill 显示名称解耦
    - 新 Skill：插入
    - 已有 Skill：name / description / system_prompt 有变化时更新
    - 孤儿 Skill：MD 文件已删除的内置 Skill 自动从数据库删除
    - 默认 Skill：仅在 default_skill_id 未设置时写入，不覆盖用户的选择
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from astracore.adapters.db.models import SkillRow, UserSettingsRow
    from astracore.adapters.db.session import get_session

    await _ensure_source_key_column(db_url)

    builtin_skills = _load_builtin_skills()
    active_keys = {s["source_key"] for s in builtin_skills}

    async with get_session(db_url) as db:
        result = await db.execute(select(SkillRow).where(SkillRow.is_builtin == True))  # noqa: E712
        existing: dict[str, SkillRow] = {
            row.source_key: row for row in result.scalars().all() if row.source_key
        }
        # source_key 为空的旧记录，按 name 索引备用
        existing_by_name: dict[str, SkillRow] = {
            row.name: row for row in result.scalars().all() if not row.source_key
        }

        skill_ids: dict[str, str] = {}

        for skill in builtin_skills:
            key = skill["source_key"]
            name = skill["name"]

            if key in existing:
                row = existing[key]
            elif name in existing_by_name:
                # 旧数据库没有 source_key，按 name 匹配并补写 key
                row = existing_by_name[name]
                row.source_key = key
            else:
                row = None

            if row is None:
                now = datetime.now(UTC)
                row = SkillRow(
                    id=str(uuid4()),
                    name=name,
                    description=skill["description"],
                    system_prompt=skill["system_prompt"],
                    is_builtin=True,
                    source_key=key,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.flush()
                logger.debug("新增内置 Skill: %s (%s)", name, key)
            else:
                changed = (
                    row.name != name
                    or row.description != skill["description"]
                    or row.system_prompt != skill["system_prompt"]
                )
                if changed:
                    row.name = name
                    row.description = skill["description"]
                    row.system_prompt = skill["system_prompt"]
                    row.updated_at = datetime.now(UTC)
                    logger.debug("更新内置 Skill: %s (%s)", name, key)

            skill_ids[key] = row.id

        # 删除孤儿内置 Skill：
        #   1. 有 source_key 但 MD 文件已删除
        #   2. 没有 source_key 且 name 也不在当前 Skill 列表（历史遗留数据）
        active_names = {s["name"] for s in builtin_skills}
        for key, row in existing.items():
            if key not in active_keys:
                logger.info("删除孤儿内置 Skill: %s (%s)", row.name, key)
                await db.delete(row)
        for name, row in existing_by_name.items():
            if name not in active_names:
                logger.info("删除历史遗留内置 Skill: %s", name)
                await db.delete(row)

        # 仅在 default_skill_id 未设置时自动写入，不覆盖用户的选择
        settings_row = await db.get(UserSettingsRow, "default_skill_id")
        if settings_row is None or not settings_row.value:
            default_skill = next((s for s in builtin_skills if s["default"]), None)
            if default_skill and default_skill["source_key"] in skill_ids:
                default_id = skill_ids[default_skill["source_key"]]
                if settings_row is None:
                    db.add(
                        UserSettingsRow(
                            key="default_skill_id",
                            value=default_id,
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    settings_row.value = default_id
                    settings_row.updated_at = datetime.now(UTC)
                logger.info("默认 Skill 已设置: %s (%s)", default_skill["name"], default_id)

        await db.commit()

    logger.info("内置 Skill 同步完成，共 %d 条", len(builtin_skills))
