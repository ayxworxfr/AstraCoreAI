"""Skill tool adapters: load_skill, get_skill_reference, run_skill_script."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from astracore.infrastructure.tools.native import NativeToolAdapter
from astracore.modules.tools.ports.tool import ToolParameter, ToolParameterType
from astracore.shared.observability.logger import get_logger

logger = get_logger(__name__)


def build_skill_tools_adapter(db_url: str) -> NativeToolAdapter:
    """Construct the skill tool adapter exposing load_skill / get_skill_reference / run_skill_script."""
    from sqlalchemy import select  # noqa: PLC0415

    from astracore.infrastructure.db.models import SkillReferenceRow, SkillRow  # noqa: PLC0415
    from astracore.infrastructure.db.session import get_session  # noqa: PLC0415

    async def _resolve_skill_row(skill_name: str) -> SkillRow | None:
        async with get_session(db_url) as db:
            result = await db.execute(select(SkillRow).where(SkillRow.name == skill_name).limit(1))
            row = result.scalar_one_or_none()
            if row is None:
                # Fallback: treat skill_name as primary key
                row = await db.get(SkillRow, skill_name)
            return row

    async def _load_skill(skill_id: str) -> str:
        """Load skill instructions, reference list, and script list."""
        async with get_session(db_url) as db:
            result = await db.execute(select(SkillRow).where(SkillRow.name == skill_id).limit(1))
            row = result.scalar_one_or_none()
            if row is None:
                row = await db.get(SkillRow, skill_id)
            if row is None:
                return f"未找到技能「{skill_id}」。请使用系统提示「可用技能」列表中的名称。"

            refs_result = await db.execute(
                select(SkillReferenceRow)
                .where(SkillReferenceRow.skill_id == row.id)
                .order_by(SkillReferenceRow.sort_order)
            )
            refs = list(refs_result.scalars().all())

        logger.info(
            "load_skill: %s (display_name=%s, refs=%d)", row.name, row.display_name, len(refs)
        )
        parts: list[str] = [row.instructions or ""]

        if refs:
            ref_lines = [
                f"- `{r.source_file}`" + (f"：{r.description}" if r.description else "")
                for r in refs
            ]
            parts.append(
                "## 可用参考文档\n\n"
                "使用 `get_skill_reference` 工具按文件路径加载内容。\n\n" + "\n".join(ref_lines)
            )

        if row.skill_dir:
            scripts_dir = Path(row.skill_dir) / "scripts"
            if scripts_dir.exists():
                scripts = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
                if scripts:
                    parts.append(
                        "## 可用脚本\n\n"
                        "使用 `run_skill_script` 工具按脚本名称执行。\n\n"
                        + "\n".join(f"- `{s}`" for s in scripts)
                    )

        return "\n\n---\n\n".join(p for p in parts if p)

    async def _get_skill_reference(skill_id: str, file: str) -> str:
        """Load a reference document by its source_file path."""
        async with get_session(db_url) as db:
            skill_result = await db.execute(
                select(SkillRow).where(SkillRow.name == skill_id).limit(1)
            )
            skill_row = skill_result.scalar_one_or_none()
            if skill_row is None:
                skill_row = await db.get(SkillRow, skill_id)
            if skill_row is None:
                return f"未找到技能「{skill_id}」。"

            ref_result = await db.execute(
                select(SkillReferenceRow).where(
                    SkillReferenceRow.skill_id == skill_row.id,
                    SkillReferenceRow.source_file == file,
                )
            )
            ref_row = ref_result.scalar_one_or_none()

        if ref_row is None:
            return f"未找到参考文档「{file}」（技能：{skill_id}）。"
        logger.info("get_skill_reference: %s / %s", skill_id, file)
        return ref_row.content

    async def _run_skill_script(skill_id: str, script: str, args: dict | None = None) -> str:
        """Execute a script in the skill's scripts/ directory."""
        import asyncio  # noqa: PLC0415

        row = await _resolve_skill_row(skill_id)
        if row is None:
            return json.dumps({"error": f"未找到技能「{skill_id}」"}, ensure_ascii=False)
        if not row.skill_dir:
            return json.dumps({"error": f"技能「{skill_id}」没有关联目录"}, ensure_ascii=False)

        skill_path = Path(row.skill_dir)
        scripts_dir = skill_path / "scripts"
        script_path = (scripts_dir / script).resolve()

        # Path traversal guard
        try:
            script_path.relative_to(scripts_dir.resolve())
        except ValueError:
            return json.dumps({"error": "非法脚本路径"}, ensure_ascii=False)

        if not script_path.exists():
            return json.dumps({"error": f"脚本不存在：{script}"}, ensure_ascii=False)

        suffix = script_path.suffix.lower()
        arg_str = json.dumps(args or {}, ensure_ascii=False)
        if suffix == ".py":
            cmd = ["python", str(script_path), arg_str]
        elif suffix == ".js":
            cmd = ["node", str(script_path), arg_str]
        else:
            cmd = [str(script_path), arg_str]

        logger.info("run_skill_script: cmd=%s", cmd)
        start = time.monotonic()
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                cwd=str(skill_path),
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "run_skill_script: %s / %s (exit=%d, %dms)",
                skill_id,
                script,
                result.returncode,
                duration_ms,
            )
            return json.dumps(
                {
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": duration_ms,
                },
                ensure_ascii=False,
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "脚本执行超时（30 秒）"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("脚本执行失败: %s / %s", skill_id, script)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    adapter = NativeToolAdapter()

    adapter.register_tool(
        name="load_skill",
        func=_load_skill,
        description=(
            "按技能名称加载该技能的完整说明（instructions）、可用参考文档列表和可用脚本列表。"
            "在需要激活某个技能时调用，技能名称来自系统提示「可用技能」列表。"
        ),
        parameters=[
            ToolParameter(
                name="skill_id",
                type=ToolParameterType.STRING,
                description="技能名称（kebab-case），与系统提示「可用技能」列表中的名称完全一致",
                required=True,
            )
        ],
    )

    adapter.register_tool(
        name="get_skill_reference",
        func=_get_skill_reference,
        description=(
            "按文件路径加载技能附属参考文档的内容。"
            "文件路径来自 load_skill 返回的「可用参考文档」列表。"
        ),
        parameters=[
            ToolParameter(
                name="skill_id",
                type=ToolParameterType.STRING,
                description="技能名称（kebab-case）",
                required=True,
            ),
            ToolParameter(
                name="file",
                type=ToolParameterType.STRING,
                description="参考文档的相对路径（如 references/craft/writing-craft.md）",
                required=True,
            ),
        ],
    )

    adapter.register_tool(
        name="run_skill_script",
        func=_run_skill_script,
        description=(
            "执行技能目录下 scripts/ 中的脚本。脚本名称来自 load_skill 返回的「可用脚本」列表。"
        ),
        parameters=[
            ToolParameter(
                name="skill_id",
                type=ToolParameterType.STRING,
                description="技能名称（kebab-case）",
                required=True,
            ),
            ToolParameter(
                name="script",
                type=ToolParameterType.STRING,
                description="脚本文件名（不含路径，如 fix-dashes.js）",
                required=True,
            ),
            ToolParameter(
                name="args",
                type=ToolParameterType.OBJECT,
                description="传给脚本的参数（JSON 对象），可选",
                required=False,
            ),
        ],
    )

    return adapter
