"""SessionContext value object — structured dynamic prompt segment."""

from datetime import datetime, timedelta, timezone

from astracore.modules.chat.domain.session_context import (
    SessionContext,
    as_openai_session_message_content,
    as_session_text,
    build_tool_progress_xml,
    coerce_session_context,
)

_BJ = timezone(timedelta(hours=8))


def test_build_includes_datetime_and_optional_layers():
    fixed = datetime(2026, 8, 5, 10, 0, tzinfo=_BJ)
    ctx = SessionContext.build(
        turn_context="喜欢简洁回答",
        active_skill="writing-coach",
        rag_context="<knowledge>doc</knowledge>",
        now=fixed,
    )
    xml = ctx.render()
    assert xml.startswith("<session_context>")
    assert 'today="2026-08-05"' in xml
    assert "now=" not in xml
    assert "<knowledge>doc</knowledge>" in xml
    assert 'name="writing-coach"' in xml
    assert "喜欢简洁回答" in xml
    assert "external_data" in xml
    assert "<tool_progress>" not in xml


def test_with_tool_round_does_not_mutate_original():
    base = SessionContext.build(now=datetime(2026, 8, 5, 10, 0, tzinfo=_BJ))
    r1 = base.with_tool_round(iteration=1, max_iterations=5)
    r2 = base.with_tool_round(iteration=2, max_iterations=5)
    assert base.tool_progress_xml == ""
    assert "第 1/5 轮" in r1.render()
    assert "第 2/5 轮" in r2.render()
    assert r1.datetime_xml == r2.datetime_xml == base.datetime_xml


def test_closing_tool_progress():
    xml = build_tool_progress_xml(iteration=3, max_iterations=5, closing=True)
    assert "禁止继续调用工具" in xml


def test_as_session_text_and_openai_frame():
    ctx = SessionContext.build(now=datetime(2026, 8, 5, 10, 0, tzinfo=_BJ))
    plain = as_session_text(ctx)
    framed = as_openai_session_message_content(ctx)
    assert plain is not None and plain.startswith("<session_context>")
    assert framed is not None
    assert framed.startswith("以下是当前回合的会话上下文")
    assert "<session_context>" in framed
    assert as_session_text(None) is None
    assert as_openai_session_message_content("  raw  ") == "raw"


def test_coerce_session_context_defaults_to_datetime_only():
    ctx = coerce_session_context(None)
    assert isinstance(ctx, SessionContext)
    assert ctx.datetime_xml
    assert coerce_session_context(ctx) is ctx
