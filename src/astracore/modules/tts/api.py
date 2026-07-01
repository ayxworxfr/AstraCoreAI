"""TTS synthesis API — proxies to Microsoft Edge TTS for neural voice quality."""

import asyncio
from collections.abc import AsyncGenerator

import edge_tts
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from astracore.infrastructure.db.models import UserRow
from astracore.modules.auth.dependencies import get_current_user

router = APIRouter()

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

_voice_names: set[str] | None = None
_voice_names_lock = asyncio.Lock()


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    voice: str = DEFAULT_VOICE
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)


def _rate_str(rate: float) -> str:
    pct = round((rate - 1.0) * 100)
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _pitch_str(pitch: float) -> str:
    hz = round((pitch - 1.0) * 100)
    return f"+{hz}Hz" if hz >= 0 else f"{hz}Hz"


def resolve_voice(voice: str, available: set[str], default: str = DEFAULT_VOICE) -> str:
    """已下线或未知音色回落默认，避免 edge-tts 抛 NoAudioReceived。"""
    return voice if voice in available else default


async def _get_voice_names() -> set[str]:
    global _voice_names
    if _voice_names is not None:
        return _voice_names

    async with _voice_names_lock:
        if _voice_names is None:
            voices = await edge_tts.list_voices()
            _voice_names = {voice["ShortName"] for voice in voices}
    return _voice_names


async def _stream_audio(communicate: edge_tts.Communicate) -> AsyncGenerator[bytes, None]:
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


@router.post("/synthesize")
async def synthesize(
    body: SynthesizeRequest,
    _current_user: UserRow = Depends(get_current_user),
) -> StreamingResponse:
    available = await _get_voice_names()
    voice = resolve_voice(body.voice, available)
    communicate = edge_tts.Communicate(
        body.text,
        voice,
        rate=_rate_str(body.rate),
        pitch=_pitch_str(body.pitch),
    )
    return StreamingResponse(
        _stream_audio(communicate),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )
