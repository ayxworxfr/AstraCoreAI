"""启动时执行数据初始化：向量库文档写入 + 内置 Skill 写入。

- docs/    目录下的 .md 文件写入向量数据库，新增文档只需放文件即可
- skills/  目录下每个子目录对应一个内置 Skill：
    <skill-name>/
        SKILL.md          — 必须存在；frontmatter + system_prompt 正文
        <ref>.md          — 可选；附属参考文档，LLM 按需加载
  SKILL.md frontmatter 字段:
    name        必填，Skill 显示名称
    description 选填，简短描述
    order       选填，排序值（整数），越小越靠前，默认 1000
    default     选填，true 表示首次启动时设为默认 Skill
    references  选填，YAML 列表，每项包含 title / description / file
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from astracore.runtime.observability.logger import get_logger

logger = get_logger(__name__)

DOCS_DIR = Path(__file__).parent / "docs"
SKILLS_DIR = Path(__file__).parent / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title\s*:\s*(.+)$", re.MULTILINE)


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
        result = await pipeline.retriever.index_document(
            document_id=document_id,
            text=content,
            metadata={"title": title, "source": "seed"},
        )
        if result.success:
            success_count += 1
            logger.debug("种子文档写入成功: %s (%s)", document_id, title)
        else:
            if result.error:
                logger.warning("种子文档写入失败: %s - %s", document_id, result.error)
            else:
                logger.warning("种子文档写入失败: %s", document_id)

    logger.info("种子文档写入完成: %d/%d 成功", success_count, len(md_files))


# ---------------------------------------------------------------------------
# Skill 种子（SQLite）
# ---------------------------------------------------------------------------


def _parse_skill_dir(skill_dir: Path) -> dict:
    """解析 skill 子目录，返回包含 Skill 元数据和引用列表的 dict。

    目录结构：
      <skill-name>/
        SKILL.md     — 必须存在，frontmatter + system_prompt 正文
        <ref>.md     — 可选附属参考文档

    SKILL.md frontmatter 使用 yaml.safe_load 解析，支持列表类型字段（references）。
    source_key 取目录名，作为跨重启的稳定标识符。
    """
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"Skill 目录缺少 SKILL.md: {skill_dir}")

    raw = skill_file.read_text(encoding="utf-8")
    meta: dict = {}
    system_prompt = raw.strip()

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        parsed = yaml.safe_load(fm_match.group(1))
        if isinstance(parsed, dict):
            meta = parsed
        system_prompt = raw[fm_match.end():].strip()

    if not meta.get("name"):
        raise ValueError(f"SKILL.md 缺少 name 字段: {skill_file}")
    try:
        sort_order = int(meta.get("order", 1000))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"SKILL.md order 字段必须是整数: {skill_file}") from exc

    # 解析 references 列表（可选）
    raw_refs = meta.get("references") or []
    references: list[dict] = []
    for idx, entry in enumerate(raw_refs):
        if not isinstance(entry, dict):
            logger.warning("跳过非法 reference 条目（非 dict）: %s[%d]", skill_file, idx)
            continue
        title = str(entry.get("title") or "").strip()
        file_name = str(entry.get("file") or "").strip()
        if not title or not file_name:
            logger.warning("跳过缺少 title 或 file 的 reference 条目: %s[%d]", skill_file, idx)
            continue
        ref_path = skill_dir / file_name
        if not ref_path.exists():
            logger.warning("reference 文件不存在，跳过: %s", ref_path)
            continue
        references.append({
            "title": title,
            "description": str(entry.get("description") or "").strip(),
            "content": ref_path.read_text(encoding="utf-8").strip(),
            "source_file": file_name,
            "sort_order": idx,
        })

    return {
        "source_key": skill_dir.name,
        "name": str(meta["name"]),
        "description": str(meta.get("description") or ""),
        "order": sort_order,
        "system_prompt": system_prompt,
        "default": str(meta.get("default") or "false").lower() == "true",
        "references": references,
    }


def _load_builtin_skills(extra_dirs: list[str] | None = None) -> list[dict]:
    """按 frontmatter order 加载 skills/ 目录及所有额外配置目录下的 Skill 子目录。

    每个子目录内必须有 SKILL.md。extra_dirs 中的目录按顺序追加在内置目录之后；
    source_key 冲突时后加载的目录覆盖先加载的并记录警告。
    """
    dirs_to_scan: list[Path] = []
    if SKILLS_DIR.exists():
        dirs_to_scan.append(SKILLS_DIR)
    else:
        logger.warning("内置 skills 目录不存在: %s", SKILLS_DIR)

    for raw in (extra_dirs or []):
        p = Path(raw).expanduser().resolve()
        if p.exists() and p.is_dir():
            dirs_to_scan.append(p)
        else:
            logger.warning("配置的 skill 目录不存在，跳过: %s", p)

    seen: dict[str, Path] = {}  # source_key -> dir path（用于冲突日志）
    skills_by_key: dict[str, dict] = {}

    for dir_path in dirs_to_scan:
        for skill_dir in sorted(p for p in dir_path.iterdir() if p.is_dir()):
            if not (skill_dir / "SKILL.md").exists():
                continue
            source_key = skill_dir.name
            if source_key in seen:
                logger.warning(
                    "Skill source_key 冲突: '%s' 出现在 %s 和 %s，后者覆盖",
                    source_key, seen[source_key], skill_dir,
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

    from astracore.adapters.db.session import get_engine

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        # 补齐 skills 表列（向后兼容已有数据库）
        for col_name, ddl in [
            ("source_key", "TEXT"),
            ("sort_order", "INTEGER NOT NULL DEFAULT 1000"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {col_name} {ddl}"))
                logger.info("已为 skills 表添加 %s 列", col_name)
            except Exception:
                pass  # 列已存在

        # 创建 skill_references 表（若不存在）
        await conn.execute(text("""
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
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_skill_references_skill_id ON skill_references (skill_id)"
        ))


async def seed_builtin_skills(db_url: str, extra_skill_dirs: list[str] | None = None) -> None:
    """写入并同步内置 Skill 及其附属 reference 文档，首次启动时设置默认 Skill。

    - 匹配键：source_key（目录名），与 Skill 显示名称解耦
    - 新 Skill：插入
    - 已有 Skill：name / description / system_prompt / sort_order 有变化时更新
    - 孤儿 Skill：目录已删除的内置 Skill 自动从数据库删除（CASCADE 删除关联 references）
    - References：按 (skill_id, title) 做 upsert，孤儿 reference（SKILL.md 中已移除）自动删除
    - 默认 Skill：仅在 default_skill_id 未设置时写入，不覆盖用户的选择
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from astracore.adapters.db.models import SkillReferenceRow, SkillRow, UserSettingsRow
    from astracore.adapters.db.session import get_session

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
                    db.add(SkillReferenceRow(
                        id=str(uuid4()),
                        skill_id=skill_id,
                        title=title,
                        description=ref["description"],
                        content=ref["content"],
                        source_file=ref["source_file"],
                        sort_order=ref["sort_order"],
                        created_at=now,
                        updated_at=now,
                    ))
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

            # 删除孤儿 references（SKILL.md 中已移除的条目）
            for title, ref_row in existing_refs.items():
                if title not in active_ref_titles:
                    logger.info("删除孤儿 reference: %s / %s", key, title)
                    await db.delete(ref_row)

        # 删除目录已删除的内置 Skill（CASCADE 自动清理关联 references）
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
                    db.add(UserSettingsRow(
                        key="default_skill_id",
                        value=default_id,
                        updated_at=datetime.now(UTC),
                    ))
                else:
                    settings_row.value = default_id
                    settings_row.updated_at = datetime.now(UTC)
                logger.info("默认 Skill 已设置: %s (%s)", default_skill["name"], default_id)

        await db.commit()

    logger.info("内置 Skill 同步完成，共 %d 条", len(builtin_skills))
