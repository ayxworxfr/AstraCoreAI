"""Prompt building utilities shared between the HTTP service and the SDK."""

from datetime import datetime, timedelta, timezone

_BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


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
            f"- 当用户提到\"今天\"时，指的是{today_text}",
        ]
    )


def render_skill_prompt(prompt: str, ai_name: str, owner_name: str) -> str:
    """将 system_prompt 中的动态占位符替换为请求时的上下文。"""
    result = prompt.replace("{{ai_name}}", ai_name or "AI 助手")
    result = result.replace("{{owner_name}}", owner_name or "用户")
    result = result.replace("{{current_time_info}}", build_current_time_info())
    return result
