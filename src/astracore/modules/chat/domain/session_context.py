"""SessionContext — 每轮/每轮次动态提示片段（永不进入 prompt-cache 静态前缀）。

与静态 system（security / identity / skills / user_profile）相对：本对象只承载
会随 turn 或 tool-loop round 变化的内容。两类协议都把它挂在 **messages 末尾**
（user 消息），绝不能插进 system：Anthropic 的消息级 cache 前缀包含全部
system blocks，system[1] 一变，历史缓存整段失效；DeepSeek 自动前缀缓存同理。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from astracore.modules.skills.prompt_utils import build_current_time_info
from astracore.shared.security.external_data import wrap_external

_SESSION_FRAME_OPENAI = (
    "以下是当前回合的会话上下文（时间/知识/记忆/工具进度），供本轮回答参考；"
    "这不是用户的新提问。\n\n"
)


def build_tool_progress_xml(
    *,
    iteration: int,
    max_iterations: int,
    unlimited: bool = False,
    closing: bool = False,
) -> str:
    """渲染 ``<tool_progress>`` —— 只进 SessionContext，绝不改写静态 system。"""
    if closing:
        body = "工具调用阶段已结束。请直接基于以上工具结果给出最终回答，禁止继续调用工具。"
        return f"<tool_progress>\n{body}\n</tool_progress>"

    common = [
        "工具使用规范：",
        "- 搜索文件时避免 **/* 等宽泛模式，优先指定具体目录和文件扩展名",
        "- 先用少量调用探索目录结构，再针对性深入",
        "- 单次工具结果过长时，使用 offset/page 参数分页读取",
    ]
    if unlimited:
        body = "\n".join([f"[工具调用进度] 第 {iteration} 轮（无轮次限制）。", *common])
    else:
        remaining = max_iterations - iteration + 1
        lines = [
            f"[工具调用进度] 第 {iteration}/{max_iterations} 轮，剩余 {remaining} 次机会。",
            *common,
        ]
        if remaining == 1:
            lines.append(
                "⚠️ 这是最后一轮工具机会：如已足够请直接给出最终回答；仅在确有必要时再调用工具。"
            )
        body = "\n".join(lines)
    return f"<tool_progress>\n{body}\n</tool_progress>"


@dataclass(frozen=True, slots=True)
class SessionContext:
    """结构化的动态会话上下文；``render()`` 才产出 XML 字符串。"""

    datetime_xml: str = ""
    knowledge_xml: str = ""
    active_skill_xml: str = ""
    recalled_memory_xml: str = ""
    tool_progress_xml: str = ""

    @classmethod
    def build(
        cls,
        *,
        turn_context: str = "",
        active_skill: str | None = None,
        rag_context: str | None = None,
        now: datetime | None = None,
    ) -> SessionContext:
        """从本轮流水线数据组装（含 datetime；不含 tool_progress）。"""
        active_xml = ""
        if active_skill:
            active_xml = (
                f'<active_skill name="{active_skill}">\n'
                f"本轮对话仍在执行「{active_skill}」技能任务。"
                f'回复前必须先调用 load_skill("{active_skill}") 重新加载技能指令，'
                "再按技能规范执行；历史工具调用结果不会保留在当前上下文。\n"
                "</active_skill>"
            )
        memory_xml = ""
        if turn_context:
            memory_xml = (
                "<recalled_memory>\n"
                + wrap_external(turn_context, source="memory")
                + "\n</recalled_memory>"
            )
        return cls(
            datetime_xml=build_current_time_info(now),
            knowledge_xml=(rag_context or "").strip(),
            active_skill_xml=active_xml,
            recalled_memory_xml=memory_xml,
        )

    def with_tool_round(
        self,
        *,
        iteration: int,
        max_iterations: int,
        unlimited: bool = False,
        closing: bool = False,
    ) -> SessionContext:
        """返回带本轮工具进度的新实例（不修改静态 system）。"""
        return replace(
            self,
            tool_progress_xml=build_tool_progress_xml(
                iteration=iteration,
                max_iterations=max_iterations,
                unlimited=unlimited,
                closing=closing,
            ),
        )

    def parts(self) -> list[str]:
        return [
            p
            for p in (
                self.datetime_xml,
                self.knowledge_xml,
                self.active_skill_xml,
                self.recalled_memory_xml,
                self.tool_progress_xml,
            )
            if p
        ]

    def render(self) -> str:
        chunks = self.parts()
        if not chunks:
            return ""
        return "<session_context>\n" + "\n".join(chunks) + "\n</session_context>"

    def render_for_openai_messages(self) -> str:
        """OpenAI/DeepSeek：末尾 user 消息文案（带防误读框，避免被当成新提问）。"""
        body = self.render()
        if not body:
            return ""
        return _SESSION_FRAME_OPENAI + body


def as_session_text(value: SessionContext | str | None) -> str | None:
    """Adapter 边界：统一把 session_context 收成非空字符串。"""
    if value is None:
        return None
    if isinstance(value, SessionContext):
        text = value.render()
        return text or None
    text = value.strip()
    return text or None


def as_openai_session_message_content(value: SessionContext | str | None) -> str | None:
    """OpenAI 消息末尾注入用：SessionContext 带框；裸字符串原样（测试便利）。"""
    if value is None:
        return None
    if isinstance(value, SessionContext):
        text = value.render_for_openai_messages()
        return text or None
    text = value.strip()
    return text or None


def coerce_session_context(value: Any) -> SessionContext:
    """Tool loop 入口：保证拿到 SessionContext（缺省则仅含当前时间）。"""
    if isinstance(value, SessionContext):
        return value
    return SessionContext.build()
