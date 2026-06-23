"""TTS synthesis API — proxies to Microsoft Edge TTS for neural voice quality."""

from collections.abc import AsyncGenerator

import edge_tts
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from astracore.infrastructure.db.models import UserRow
from astracore.modules.auth.dependencies import get_current_user

router = APIRouter()


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)


def _rate_str(rate: float) -> str:
    pct = round((rate - 1.0) * 100)
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _pitch_str(pitch: float) -> str:
    hz = round((pitch - 1.0) * 100)
    return f"+{hz}Hz" if hz >= 0 else f"{hz}Hz"


async def _stream_audio(communicate: edge_tts.Communicate) -> AsyncGenerator[bytes, None]:
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


@router.post("/synthesize")
async def synthesize(
    body: SynthesizeRequest,
    _current_user: UserRow = Depends(get_current_user),
) -> StreamingResponse:
    communicate = edge_tts.Communicate(
        body.text,
        body.voice,
        rate=_rate_str(body.rate),
        pitch=_pitch_str(body.pitch),
    )
    return StreamingResponse(
        _stream_audio(communicate),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )
