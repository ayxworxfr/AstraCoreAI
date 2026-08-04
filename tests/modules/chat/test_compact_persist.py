"""Compact 摘要必须能被 _prepare_for_save 保留。"""

from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.pipeline import _prepare_for_save


def test_compacted_summary_survives_prepare_for_save():
    summary = Message(
        role=MessageRole.USER,
        content="[记忆同步]\n任务进度：改 auth",
        metadata={"synthetic": True, "compacted": True},
    )
    user = Message(role=MessageRole.USER, content="继续")
    assistant = Message(role=MessageRole.ASSISTANT, content="好的")
    saved = _prepare_for_save([summary, user, assistant])

    assert any(m.metadata.get("compacted") for m in saved)
    assert any("改 auth" in (m.content or "") for m in saved)


def test_plain_synthetic_still_dropped():
    synthetic = Message(
        role=MessageRole.USER,
        content="[记忆快照] secret",
        metadata={"synthetic": True},
    )
    user = Message(role=MessageRole.USER, content="hi")
    saved = _prepare_for_save([synthetic, user])
    assert len(saved) == 1
    assert saved[0].content == "hi"
