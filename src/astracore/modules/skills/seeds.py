"""启动时执行数据初始化：向量库文档写入 + 内置 Skill 写入。

- modules/rag/knowledge_base/ 目录及子目录下的 .md 文件写入向量数据库，新增文档只需放文件即可
- modules/skills/builtin/ 目录下每个子目录对应一个内置 Skill（Agent Skills 标准格式）：
    <skill-name>/
        SKILL.md          — 必须存在；frontmatter + instructions 正文
        references/       — 可选；附属参考文档（递归扫描所有 .md 文件）
        scripts/          — 可选；可执行脚本
  SKILL.md frontmatter 字段（Agent Skills 开放标准）:
    name        必填，kebab-case，与目录名一致
    description 必填，描述"做什么 + 何时使用"
    metadata:
      display_name  必填，人类可读显示名称（如"通用助手"）
      order         选填，排序值（数字字符串），越小越靠前，默认 1000
      category      选填，分类标签（general / coding / writing / analysis / finance / language / ops 等）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from astracore.shared.observability.logger import get_logger

logger = get_logger(__name__)

MODULES_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = MODULES_DIR / "rag" / "knowledge_base"
SKILLS_DIR = Path(__file__).parent / "builtin"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# 文档种子（向量库）
# ---------------------------------------------------------------------------


def _parse_doc_md(path: Path, base_dir: Path) -> tuple[str, str, dict[str, Any]]:
    """解析文档 .md，返回 (document_id, content, metadata)。

    document_id 为相对 base_dir 的路径（不含扩展名），如 ai-basics/llm_intro。
    metadata 包含 title、category、source、path，以及可选的 tags、related 字段。
    ChromaDB 不支持 list 类型，tags/related 序列化为逗号分隔字符串。
    """
    raw = path.read_text(encoding="utf-8")
    rel_path = path.relative_to(base_dir).with_suffix("").as_posix()

    metadata: dict[str, Any] = {
        "title": path.stem,
        "category": path.parent.name if path.parent != base_dir else "general",
        "source": "knowledge_base",
        "path": rel_path,
    }
    content = raw

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        parsed = yaml.safe_load(fm_match.group(1))
        if isinstance(parsed, dict):
            if "title" in parsed:
                metadata["title"] = str(parsed["title"]).strip()
            if "category" in parsed:
                metadata["category"] = str(parsed["category"]).strip()
            if "tags" in parsed:
                tags = parsed["tags"]
                metadata["tags"] = (
                    ",".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
                )
            if "related" in parsed:
                related = parsed["related"]
                metadata["related"] = (
                    ",".join(str(r) for r in related) if isinstance(related, list) else str(related)
                )
        content = raw[fm_match.end() :]

    return rel_path, content.strip(), metadata


async def seed_documents(pipeline: object) -> None:
    """递归扫描 knowledge_base/ 目录，将所有 .md 文件写入向量数据库。幂等可重复执行。"""
    from astracore.modules.rag.application.pipeline import RAGPipeline

    assert isinstance(pipeline, RAGPipeline)

    if not DOCS_DIR.exists():
        logger.warning("knowledge_base 目录不存在: %s，跳过种子写入", DOCS_DIR)
        return

    md_files = sorted(DOCS_DIR.rglob("*.md"))
    if not md_files:
        logger.info("knowledge_base 目录为空，无文档可写入")
        return

    success_count = 0
    for path in md_files:
        document_id, content, metadata = _parse_doc_md(path, DOCS_DIR)
        result = await pipeline.retriever.index_document(
            document_id=document_id,
            text=content,
            metadata=metadata,
        )
        if result.success:
            success_count += 1
            logger.debug("知识库文档写入成功: %s (%s)", document_id, metadata["title"])
        else:
            if result.error:
                logger.warning("知识库文档写入失败: %s - %s", document_id, result.error)
            else:
                logger.warning("知识库文档写入失败: %s", document_id)

    logger.info("知识库文档写入完成: %d/%d 成功", success_count, len(md_files))


# ---------------------------------------------------------------------------
# Skill 种子（SQLite）
# ---------------------------------------------------------------------------


def _parse_skill_dir(skill_dir: Path) -> dict[str, Any]:
    """解析 skill 子目录，返回包含 Skill 元数据和引用列表的 dict。

    目录结构（Agent Skills 标准格式）：
      <skill-name>/
        SKILL.md           — 必须存在，frontmatter + instructions 正文
        references/        — 可选，附属参考文档（递归扫描 .md 文件）
        scripts/           — 可选，可执行脚本

    frontmatter 中的 name 字段必须与目录名一致（kebab-case）。
    source_key 取目录名，作为跨重启的稳定标识符。
    """
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"Skill 目录缺少 SKILL.md: {skill_dir}")

    raw = skill_file.read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    instructions = raw.strip()

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        parsed = yaml.safe_load(fm_match.group(1))
        if isinstance(parsed, dict):
            meta = parsed
        instructions = raw[fm_match.end() :].strip()

    name = str(meta.get("name") or skill_dir.name)
    description = str(meta.get("description") or "").strip()

    skill_metadata = meta.get("metadata") or {}
    if not isinstance(skill_metadata, dict):
        skill_metadata = {}

    display_name = str(skill_metadata.get("display_name") or "").strip()
    try:
        sort_order = int(str(skill_metadata.get("order") or meta.get("order") or "1000"))
    except (ValueError, TypeError):
        sort_order = 1000
    category_raw = skill_metadata.get("category")
    category = str(category_raw).strip() if category_raw else None

    # 自动发现 references/ 目录下的所有 .md 文件
    references: list[dict[str, Any]] = []
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        for ref_path in sorted(refs_dir.rglob("*.md")):
            rel_path = ref_path.relative_to(skill_dir).as_posix()
            # use relative path without extension as title (unique within skill)
            title = ref_path.relative_to(skill_dir).with_suffix("").as_posix()
            try:
                content = ref_path.read_text(encoding="utf-8").strip()
            except Exception:
                logger.warning("无法读取参考文档，跳过: %s", ref_path)
                continue
            references.append(
                {
                    "title": title,
                    "description": "",
                    "content": content,
                    "source_file": rel_path,
                    "sort_order": 0,
                }
            )

    return {
        "source_key": skill_dir.name,
        "name": name,
        "display_name": display_name,
        "description": description,
        "instructions": instructions,
        "category": category,
        "order": sort_order,
        "skill_dir": skill_dir.as_posix(),
        "references": references,
    }


def _load_builtin_skills(extra_dirs: list[str] | None = None) -> list[dict[str, Any]]:
    """按 metadata.order 加载 skills/ 目录及所有额外配置目录下的 Skill 子目录。

    每个子目录内必须有 SKILL.md。extra_dirs 中的目录按顺序追加在内置目录之后；
    source_key 冲突时后加载的目录覆盖先加载的并记录警告。
    """
    dirs_to_scan: list[Path] = []
    if SKILLS_DIR.exists():
        dirs_to_scan.append(SKILLS_DIR)
    else:
        logger.warning("内置 skills 目录不存在: %s", SKILLS_DIR)

    for raw in extra_dirs or []:
        p = Path(raw).expanduser().resolve()
        if p.exists() and p.is_dir():
            dirs_to_scan.append(p)
        else:
            logger.warning("配置的 skill 目录不存在，跳过: %s", p)

    seen: dict[str, Path] = {}  # source_key -> dir path（用于冲突日志）
    skills_by_key: dict[str, dict[str, Any]] = {}

    for dir_path in dirs_to_scan:
        for skill_dir in sorted(p for p in dir_path.iterdir() if p.is_dir()):
            if not (skill_dir / "SKILL.md").exists():
                continue
            source_key = skill_dir.name
            if source_key in seen:
                logger.warning(
                    "Skill source_key 冲突: '%s' 出现在 %s 和 %s，后者覆盖",
                    source_key,
                    seen[source_key],
                    skill_dir,
                )
            seen[source_key] = skill_dir
            try:
                skills_by_key[source_key] = _parse_skill_dir(skill_dir)
            except Exception:
                logger.exception("解析 Skill 目录失败: %s", skill_dir)

    skills = list(skills_by_key.values())
    return sorted(skills, key=lambda s: (s["order"], s["source_key"]))


async def _ensure_skill_tables(db_url: str) -> None:
    """补齐内置 Skill 同步所需列及 skill_references 表。"""
    from sqlalchemy import text

    from astracore.infrastructure.db.session import get_engine

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        # 检测旧 system_prompt 列：SQLite 不支持 DROP COLUMN，需重建表
        pragma_result = await conn.execute(text("PRAGMA table_info(skills)"))
        existing_cols = {row[1] for row in pragma_result.fetchall()}

        if "system_prompt" in existing_cols:
            # 重建 skills 表以移除 system_prompt NOT NULL 列
            await conn.execute(text("ALTER TABLE skills RENAME TO _skills_bak"))
            await conn.execute(
                text("""
                CREATE TABLE skills (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    instructions TEXT NOT NULL DEFAULT '',
                    category TEXT,
                    is_builtin INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 1000,
                    skill_dir TEXT,
                    source_key TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)
            )
            await conn.execute(
                text("""
                INSERT INTO skills
                    (id, name, display_name, description, instructions,
                     category, is_builtin, sort_order, skill_dir, source_key,
                     created_at, updated_at)
                SELECT
                    id, name,
                    COALESCE(display_name, '') AS display_name,
                    COALESCE(description, '') AS description,
                    CASE
                        WHEN COALESCE(instructions, '') != '' THEN instructions
                        WHEN COALESCE(system_prompt, '') != '' THEN system_prompt
                        ELSE ''
                    END AS instructions,
                    category,
                    COALESCE(is_builtin, 0) AS is_builtin,
                    COALESCE(sort_order, 1000) AS sort_order,
                    skill_dir, source_key,
                    created_at, updated_at
                FROM _skills_bak
            """)
            )
            await conn.execute(text("DROP TABLE _skills_bak"))
            logger.info("已将 skills 表重建迁移（移除旧 system_prompt 列）")

        # 补齐 skills 表列（向后兼容已有数据库）
        for col_name, ddl in [
            ("source_key", "TEXT"),
            ("sort_order", "INTEGER NOT NULL DEFAULT 1000"),
            ("display_name", "TEXT NOT NULL DEFAULT ''"),
            ("instructions", "TEXT NOT NULL DEFAULT ''"),
            ("category", "TEXT"),
            ("skill_dir", "TEXT"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {col_name} {ddl}"))
                logger.info("已为 skills 表添加 %s 列", col_name)
            except Exception:
                pass  # 列已存在

        # 创建 skill_references 表（若不存在）
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS skill_references (
                id TEXT PRIMARY KEY NOT NULL,
                skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                source_file TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_skill_references_skill_title UNIQUE (skill_id, title)
            )
        """)
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_skill_references_skill_id ON skill_references (skill_id)"
            )
        )


async def seed_builtin_skills(db_url: str, extra_skill_dirs: list[str] | None = None) -> None:
    """写入并同步内置 Skill 及其附属 reference 文档。

    - 匹配键：source_key（目录名），与 Skill 显示名称解耦
    - 新 Skill：插入
    - 已有 Skill：字段有变化时更新
    - 孤儿 Skill：目录已删除的内置 Skill 自动从数据库删除（CASCADE 删除关联 references）
    - References：按 (skill_id, title) 做 upsert，孤儿 reference 自动删除
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from astracore.infrastructure.db.models import SkillReferenceRow, SkillRow
    from astracore.infrastructure.db.session import get_session

    await _ensure_skill_tables(db_url)

    builtin_skills = _load_builtin_skills(extra_dirs=extra_skill_dirs)
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
            row = existing.get(key)

            if row is None:
                now = datetime.now(UTC)
                row = SkillRow(
                    id=str(uuid4()),
                    name=name,
                    display_name=skill["display_name"],
                    description=skill["description"],
                    instructions=skill["instructions"],
                    category=skill["category"],
                    is_builtin=True,
                    sort_order=skill["order"],
                    skill_dir=skill["skill_dir"],
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
                    or row.display_name != skill["display_name"]
                    or row.description != skill["description"]
                    or row.instructions != skill["instructions"]
                    or row.category != skill["category"]
                    or row.sort_order != skill["order"]
                    or row.skill_dir != skill["skill_dir"]
                )
                if changed:
                    row.name = name
                    row.display_name = skill["display_name"]
                    row.description = skill["description"]
                    row.instructions = skill["instructions"]
                    row.category = skill["category"]
                    row.sort_order = skill["order"]
                    row.skill_dir = skill["skill_dir"]
                    row.updated_at = datetime.now(UTC)
                    logger.debug("更新内置 Skill: %s (%s)", name, key)

            skill_ids[key] = row.id

            # 同步 references
            skill_id = row.id
            existing_refs_result = await db.execute(
                select(SkillReferenceRow).where(SkillReferenceRow.skill_id == skill_id)
            )
            existing_refs: dict[str, SkillReferenceRow] = {
                r.title: r for r in existing_refs_result.scalars().all()
            }
            active_ref_titles: set[str] = set()

            for ref in skill["references"]:
                title = ref["title"]
                active_ref_titles.add(title)
                ref_row = existing_refs.get(title)
                if ref_row is None:
                    now = datetime.now(UTC)
                    db.add(
                        SkillReferenceRow(
                            id=str(uuid4()),
                            skill_id=skill_id,
                            title=title,
                            description=ref["description"],
                            content=ref["content"],
                            source_file=ref["source_file"],
                            sort_order=ref["sort_order"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    logger.debug("新增 reference: %s / %s", key, title)
                else:
                    ref_changed = (
                        ref_row.description != ref["description"]
                        or ref_row.content != ref["content"]
                        or ref_row.source_file != ref["source_file"]
                        or ref_row.sort_order != ref["sort_order"]
                    )
                    if ref_changed:
                        ref_row.description = ref["description"]
                        ref_row.content = ref["content"]
                        ref_row.source_file = ref["source_file"]
                        ref_row.sort_order = ref["sort_order"]
                        ref_row.updated_at = datetime.now(UTC)
                        logger.debug("更新 reference: %s / %s", key, title)

            # 删除孤儿 references
            for title, ref_row in existing_refs.items():
                if title not in active_ref_titles:
                    logger.info("删除孤儿 reference: %s / %s", key, title)
                    await db.delete(ref_row)

        # 删除目录已删除的内置 Skill（CASCADE 自动清理关联 references）
        for key, row in existing.items():
            if key not in active_keys:
                logger.info("删除孤儿内置 Skill: %s (%s)", row.name, key)
                await db.delete(row)

        await db.commit()

    logger.info("内置 Skill 同步完成，共 %d 条", len(builtin_skills))
