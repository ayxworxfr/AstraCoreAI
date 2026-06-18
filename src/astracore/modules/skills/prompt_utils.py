"""Prompt building utilities shared between the HTTP service and the SDK."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astracore.infrastructure.db.models import SkillRow

_BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

_CATEGORY_LABELS: dict[str, str] = {
    "general": "通用",
    "coding": "编程",
    "writing": "写作",
    "analysis": "分析",
    "finance": "金融",
    "language": "语言",
    "ops": "运维",
    "entertainment": "娱乐",
}


def build_current_time_info(now: datetime | None = None) -> str:
    """生成注入给模型的当前北京时间上下文（XML 自闭合标签格式）。"""
    beijing_now = (now or datetime.now(_BEIJING_TZ)).astimezone(_BEIJING_TZ)
    iso_now = beijing_now.strftime("%Y-%m-%d %H:%M")
    today = beijing_now.strftime("%Y-%m-%d")
    weekday = _WEEKDAY_CN[beijing_now.weekday()]
    return f'<datetime now="{iso_now}" today="{today}" weekday="{weekday}" tz="Asia/Shanghai"/>'


def build_identity_layer(
    ai_name: str,
    owner_name: str,
    global_instruction: str,
) -> str:
    """Build the identity layer wrapped in <identity> XML — keeps the system prompt scannable."""
    name = ai_name or "AI 助手"
    owner = owner_name or "用户"
    inner: list[str] = [
        f"你是 {name}，{owner} 的 AI 助手。",
        build_current_time_info(),
    ]
    if global_instruction:
        inner.append(global_instruction.strip())
    body = "\n".join(inner)
    return f"<identity>\n{body}\n</identity>"


def render_skill_prompt(template: str, ai_name: str = "", owner_name: str = "") -> str:
    """Render a skill prompt template, substituting known placeholders."""
    result = template.replace("{{current_time_info}}", build_current_time_info())
    if ai_name:
        result = result.replace("{{ai_name}}", ai_name)
    if owner_name:
        result = result.replace("{{owner_name}}", owner_name)
    return result


def build_skill_manifest(skills: list[SkillRow]) -> str:
    """Build a compact L1 skill manifest (one line per skill) wrapped in <skills> XML.

    Progressive disclosure: only the *trigger surface* is shown here; the full instructions
    are loaded on demand by the model via the ``load_skill`` tool. The detailed loading
    semantics live in the ``load_skill`` tool description, not in this manifest.
    """
    if not skills:
        return ""

    grouped: dict[str, list[SkillRow]] = defaultdict(list)
    for skill in skills:
        grouped[skill.category or "general"].append(skill)

    lines: list[str] = ["<skills>"]
    for category, cat_skills in grouped.items():
        label = _CATEGORY_LABELS.get(category, category)
        for skill in cat_skills:
            display = skill.display_name or skill.name
            first_line = (skill.description or "").strip().split("\n")[0].rstrip()
            lines.append(f"- {skill.name} [{label}] {display}：{first_line}")
    lines.append("</skills>")
    return "\n".join(lines)
