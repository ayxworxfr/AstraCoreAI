"""附件加载与消息元数据编码 —— 从 pipeline 拆出，便于单测。"""

from __future__ import annotations

import base64
import dataclasses
from typing import Any

from astracore.modules.attachments.domain import AttachmentCapabilityError, AttachmentRef
from astracore.modules.attachments.ports import AttachmentStoragePort


def attachment_metadata(refs: list[AttachmentRef]) -> dict[str, Any]:
    """把已加载的 AttachmentRef 编成可 JSON 序列化的 user message metadata。"""
    if not refs:
        return {}
    return {
        "attachment_refs": [
            {
                "id": r.id,
                "mime_type": r.mime_type,
                "filename": r.filename,
                "storage_key": r.storage_key,
                "data_b64": base64.b64encode(r.data).decode("ascii") if r.data else None,
            }
            for r in refs
        ]
    }


async def load_attachment_refs(
    storage: AttachmentStoragePort | None,
    refs: list[AttachmentRef],
    profile_id: str,
    vision_capable: bool,
) -> list[AttachmentRef]:
    """能力校验后按 storage_key 加载字节；storage 为空则原样返回。"""
    if not refs:
        return []
    if not vision_capable:
        raise AttachmentCapabilityError(
            f"LLM profile '{profile_id}' does not support vision/document attachments"
        )
    if storage is None:
        return list(refs)
    loaded: list[AttachmentRef] = []
    for ref in refs:
        try:
            data = await storage.load(ref.storage_key)
        except FileNotFoundError:
            # 已删除附件占位 —— 适配器必须能处理 data=None
            loaded.append(ref)
            continue
        loaded.append(dataclasses.replace(ref, data=data))
    return loaded
