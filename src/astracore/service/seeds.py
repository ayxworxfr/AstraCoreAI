"""启动时执行数据初始化：向量库文档写入 + 内置 Skill 写入。

- docs/    目录下的 .md 文件写入向量数据库，新增文档只需放文件即可
- skills/  目录下的 .md 文件作为内置 Skill 写入 SQLite，按 order 排序
  - frontmatter 字段: name（必填）、description、order、default（true 表示首次启动时设为默认 Skill）
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


def _parse_skill_md(path: Path) -> dict:
    """解析 skill .md，返回包含 source_key / name / description / order / system_prompt / default 的 dict。

    frontmatter 支持字段：
      name        必填，Skill 显示名称
      description 选填，简短描述
      order       选填，内置 Skill 排序值，越小越靠前
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
    try:
        sort_order = int(meta.get("order", "1000"))
    except ValueError as exc:
        raise ValueError(f"Skill 文件 order 字段必须是整数: {path}") from exc

    return {
        "source_key": path.stem,
        "name": meta["name"],
        "description": meta.get("description", ""),
        "order": sort_order,
        "system_prompt": system_prompt,
        "default": meta.get("default", "false").lower() == "true",
    }


def _load_builtin_skills() -> list[dict]:
    """按 frontmatter order 加载 skills/ 目录下所有 .md 文件。"""
    if not SKILLS_DIR.exists():
        logger.warning("skills 目录不存在: %s，跳过内置 Skill 加载", SKILLS_DIR)
        return []

    skills = []
    for path in SKILLS_DIR.glob("*.md"):
        try:
            skills.append(_parse_skill_md(path))
        except Exception:
            logger.exception("解析 Skill 文件失败: %s", path)

    return sorted(skills, key=lambda skill: (skill["order"], skill["source_key"]))


async def _ensure_skill_columns(db_url: str) -> None:
    """补齐内置 Skill 同步所需列。"""
    from sqlalchemy import text

    from astracore.adapters.db.session import get_engine

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        columns = [
            ("source_key", "TEXT"),
            ("sort_order", "INTEGER NOT NULL DEFAULT 1000"),
        ]
        for name, ddl in columns:
            try:
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {name} {ddl}"))
                logger.info("已为 skills 表添加 %s 列", name)
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

    await _ensure_skill_columns(db_url)

    builtin_skills = _load_builtin_skills()
    active_keys = {s["source_key"] for s in builtin_skills}

    async with get_session(db_url) as db:
        result = await db.execute(select(SkillRow).where(SkillRow.is_builtin == True))  # noqa: E712
        existing: dict[str, SkillRow] = {
            row.source_key: row for row in result.scalars().all() if row.source_key
        }
        skill_ids: dict[str, str] = {}

        for skill in builtin_skills:
            key = skill["source_key"]
            name = skill["name"]

            if key in existing:
                row = existing[key]
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
                    sort_order=skill["order"],
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
                    or row.sort_order != skill["order"]
                )
                if changed:
                    row.name = name
                    row.description = skill["description"]
                    row.system_prompt = skill["system_prompt"]
                    row.sort_order = skill["order"]
                    row.updated_at = datetime.now(UTC)
                    logger.debug("更新内置 Skill: %s (%s)", name, key)

            skill_ids[key] = row.id

        # 删除 MD 文件已删除的内置 Skill。
        for key, row in existing.items():
            if key not in active_keys:
                logger.info("删除孤儿内置 Skill: %s (%s)", row.name, key)
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
