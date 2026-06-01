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
}


def build_current_time_info(now: datetime | None = None) -> str:
    """生成注入给模型的当前北京时间上下文。"""
    beijing_now = (now or datetime.now(_BEIJING_TZ)).astimezone(_BEIJING_TZ)
    time_text = (
        f"{beijing_now.year}年{beijing_now.month}月{beijing_now.day}日 "
        f"{beijing_now.hour:02d}:{beijing_now.minute:02d}:{beijing_now.second:02d}"
        f"（{_WEEKDAY_CN[beijing_now.weekday()]}）"
    )
    today_text = f"{beijing_now.year}年{beijing_now.month}月{beijing_now.day}日"
    return "\n".join(
        [
            "【当前时间信息】",
            f"- 北京时间：{time_text}",
            '- 当用户问"现在几点"、"什么时间"时，直接告诉用户上述时间',
            f'- 当用户提到"今天"时，指的是{today_text}',
        ]
    )


def build_identity_layer(
    ai_name: str,
    owner_name: str,
    global_instruction: str,
) -> str:
    """Build the identity layer: ai name, owner, current time, global instruction."""
    name = ai_name or "AI 助手"
    owner = owner_name or "用户"
    parts: list[str] = [
        f"你是 {name}，{owner} 的 AI 助手。",
        build_current_time_info(),
    ]
    if global_instruction:
        parts.append(global_instruction)
    return "\n\n".join(parts)


def build_skill_manifest(skills: list[SkillRow]) -> str:
    """Build a categorized skill manifest with load_skill usage hint."""
    if not skills:
        return ""

    grouped: dict[str, list[SkillRow]] = defaultdict(list)
    for skill in skills:
        grouped[skill.category or "general"].append(skill)

    lines: list[str] = [
        "## 可用技能",
        "",
        "当用户请求涉及以下技能领域时，**必须先调用 `load_skill` 加载对应技能的完整说明**，再按照说明执行任务。",
        "如果任务横跨多个技能领域，可以**依次多次调用 `load_skill`** 加载所有相关技能，综合运用。",
        "普通问候、简单常识问答无需加载技能。",
        "",
    ]
    for category, cat_skills in grouped.items():
        label = _CATEGORY_LABELS.get(category, category)
        lines.append(f"**{label} ({category})**")
        for skill in cat_skills:
            display = skill.display_name or skill.name
            first_line = (skill.description or "").strip().split("\n")[0].rstrip()
            lines.append(f"- `{skill.name}`：{display} — {first_line}")
        lines.append("")

    return "\n".join(lines).rstrip()
