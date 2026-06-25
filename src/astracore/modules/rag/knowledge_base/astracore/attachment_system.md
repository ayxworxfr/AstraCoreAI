---
title: AstraCoreAI 附件系统（图片与 PDF）
category: astracore
tags: [附件, Attachments, 图片, PDF, 多模态, vision, document, multimodal]
related: [astracore/intro, astracore/chat_pipeline, astracore/model_controls]
---

# AstraCoreAI 附件系统（图片与 PDF）

AstraCoreAI 支持在对话中上传并引用图片和 PDF 文件，跨 LLM 协议自动适配内容格式。

## 上传与引用

**上传**：`POST /api/v1/attachments`（multipart/form-data），返回 `AttachmentRef`（id + mime_type）。相同文件（SHA-256 相同）不会重复存储。

**在对话中引用**：
```python
# SDK 用法
from pathlib import Path

async with AstraCoreClient() as client:
    conv = client.conversation()
    result = await conv.send("分析这张图片", attachments=[Path("screenshot.png")])

# 也可以用已上传的 ID
attachment_id = await client.attachments.upload(Path("report.pdf"))
result = await conv.send("总结这份报告", attachments=[attachment_id])
```

**HTTP API**：在 `ChatRequest.attachments` 中传 `AttachmentRef` 列表。

## 存储结构

附件保存在 `data/attachments/<user_id>/<sha256>.<ext>`，路径由 `LocalFSAttachmentStorage` 管理。

| 端点 | 说明 |
|------|------|
| `POST /api/v1/attachments` | 上传文件（支持拖拽/粘贴/文件选择） |
| `GET /api/v1/attachments/{id}` | 查看/下载附件 |
| `DELETE /api/v1/attachments/{id}` | 删除附件 |

## 多协议适配

附件内容根据 LLM profile 的能力标志在发送时自动转换：

| 协议 | 图片 | PDF |
|------|------|-----|
| **Anthropic 原生**（`anthropic_blocks=True` 或 Anthropic Claude） | 原生 `image` content block | 原生 `document` content block（base64 PDF）|
| **OpenAI Chat Completions**（DeepSeek / GLM） | `image_url` 含 base64 data URI | pypdf 提取文本 → 注入为文字内容 |
| **OpenAI Responses API**（GPT-5） | `image_url` 含 base64 data URI | pypdf 提取文本 → 注入为文字内容 |

## 能力门控

- `LLMCapabilities.vision: bool` — 是否支持图片（前端上传入口、pipeline capability check 均由此决定）
- `LLMCapabilities.documents: bool` — 是否支持原生 PDF document block
- 不支持 vision 的 profile 会在 pipeline prepare 阶段抛出错误，不会发送附件

## 前端集成

前端聊天输入区（ChatInputArea）显示附件上传按钮的条件：`profile.capabilities.vision === true`。

支持三种上传方式：
- 点击上传区域选择文件
- 拖拽文件到输入区
- Ctrl+V 粘贴图片（剪贴板 PNG/JPEG）

附件预览在发送前展示缩略图，可删除。

## SDK AttachmentClient

`client.attachments` 是 `AttachmentClient` 的实例：

```python
# 上传文件，返回 AttachmentRef
ref = await client.attachments.upload(Path("photo.jpg"))

# 列出当前用户的附件
refs = await client.attachments.list()

# 删除
await client.attachments.delete(ref.id)
```
