from astracore.modules.tts.api import resolve_voice


def test_resolve_voice_keeps_available_voice() -> None:
    available = {"zh-CN-XiaoxiaoNeural", "en-US-AriaNeural"}
    assert resolve_voice("en-US-AriaNeural", available) == "en-US-AriaNeural"


def test_resolve_voice_falls_back_for_removed_voice() -> None:
    available = {"zh-CN-XiaoxiaoNeural"}
    assert resolve_voice("zh-CN-XiaohanNeural", available) == "zh-CN-XiaoxiaoNeural"
