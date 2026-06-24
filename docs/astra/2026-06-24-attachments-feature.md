# 附件上传功能设计卡（图片 + PDF + 跨协议适配）

日期：2026-06-24  
档位：设计卡  
子模式：large（跨多模块 + 改公开接口）

---

## 意图与边界

**Job-to-be-Done**  
当用户在聊天时需要让 AI 理解图片或 PDF 内容，用户想要在输入框旁附加文件并发送，以便 AI 能看到/读取附件并结合上下文回答，且这一能力在 SDK 与 HTTP 两种使用方式下均可用。

**Goals**
- 支持图片（jpg/png/gif/webp）和 PDF 附件上传与回放
- 跨协议适配：Anthropic（原生 image/document blocks）和 OpenAI 兼容（image_url；PDF→文本提取）
- SDK 与 HTTP 接口功能对等
- 视觉能力不足的 profile 在前端拦截 + 后端兜底 400

**Non-Goals**
- 不做 S3/MinIO 后端（首版本地 FS，但 port 接口为后续换存储留路）
- 不支持视频、音频、Office 文档（.docx/.xlsx）
- 不做附件管理 UI（列表/删除/重命名）
- 不做附件跨会话共享（每个附件与上传用户绑定）
- 不做 OCR（扫描版 PDF 提取失败时返回 400，不静默降级）

**成功标准**
1. 用户能在前端上传图片，AI 能描述图片内容（Anthropic profile）
2. 用户能在前端上传 PDF，AI 能回答 PDF 内容相关问题（Anthropic profile）
3. DeepSeek/GLM profile 下前端禁用上传按钮并提示切换模型
4. SDK 用户能 `await conv.send("描述一下", attachments=[Path("img.jpg")])` 调用
5. 历史消息回放时附件内容块能正确重建，已删除附件显示占位符

---

## 决策驱动变量

| 变量 | 类别 | 取值 | 来源 |
|---|---|---|---|
| 视觉不支持时行为 | driver | 前端拦截 + 后端 400 | 用户选择 |
| 附件存储后端 | driver | 本地 FS + `AttachmentStoragePort` 抽象 | 用户选择 |
| SDK 适配要求 | driver | SDK 与 HTTP 功能对等 | 用户明确提醒 |
| PDF 跨协议 | driver | Anthropic 原生 document block；OpenAI 走 pypdf 文本提取 | 项目事实（OpenAI adapter 无 document API） |
| 首版存储路径 | 边角 | `data/attachments/<user_id>/<sha256>.<ext>` | 假设，符合项目 data/ 目录惯例 |

---

## 项目事实（带路径引用）

### 核心接口

- `Message.content: str` + `Message.metadata: dict[str, Any]`  
  (`modules/chat/domain/message.py:40`)  
  目前内容只有 `str`，多模态块需通过 `metadata["attachment_refs"]` 携带附件引用。

- `ChatOptions` 统一 per-turn 选项，通过 `dataclasses.replace()` 传递  
  (`modules/chat/domain/chat_options.py:20`)  
  需新增 `attachments: list[AttachmentRef] = field(default_factory=list)`。

- `ChatPipeline.prepare(message, session_id, options, tool_adapter, user_id)`  
  (`modules/chat/pipeline.py:301`)  
  是 HTTP + SDK 的唯一决策点；附件加载逻辑放在此处，`ChatContext` 携带解析后的引用。

- `LLMCapabilities` 能力标志位，`infer_model_capabilities()` 按 protocol/model 推断  
  (`sdk/model_capabilities.py:6`)  
  当前无 `vision` / `documents` 字段，需新增。

### LLM 适配层

- `AnthropicAdapter._convert_messages`（`infrastructure/llm/anthropic.py:72`）  
  line 155：`converted.append({"role": msg.role.value, "content": msg.content})`  
  普通用户消息目前直接用 string content，需改为在有附件时注入 `image`/`document` 内容块列表。  
  Anthropic 已有 `list[dict]` content 的处理先例（tool_result，line 100-110），可复用同一模式。

- `OpenAIAdapter._convert_messages`（`infrastructure/llm/openai.py:87`）  
  line 103-106：`message_dict["content"] = msg.content`  
  OpenAI 视觉用 `image_url` 块（base64 data URI）；无原生 PDF API，需 `pypdf` 提取文本注入。

### 调用方 grep（改公开接口）

`ChatPipeline.prepare` 调用方：
- `modules/chat/api.py:463` — `_run_chat_in_background`
- `modules/chat/api.py:706` — `chat()` 简单端点
- `sdk/client.py:412` — `AstraCoreClient.chat_stream()`
- `sdk/client.py:458` — `AstraCoreClient.chat()`
- `sdk/client.py:659` — `WorkflowClient.executor()`

`ChatOptions` 构造方：
- `modules/chat/api.py:223` — `ChatRequest.to_options()`（HTTP）
- `sdk/client.py:372`、`:416`、`:460`、`:654` — SDK 各入口

`ChatRequest`（HTTP body）：
- `modules/chat/api.py:210`

---

## 档位

选定：**设计卡**

选档理由：改公开接口（`ChatOptions`、`ChatPipeline.prepare`、`LLMCapabilities`）、跨多模块（DB/存储/LLM适配/HTTP/SDK/前端）、引入新基础依赖（pypdf）。

---

## diff 预算

| 层 | 文件数 | 行数 |
|---|---|---|
| 后端新增（domain/port/adapter/migration/api） | 8-10 | 400-500 |
| 后端改动（adapters/pipeline/config/sdk） | 8 | 200-300 |
| 前端（上传组件/服务/store/capability guard） | 5-7 | 200-250 |
| **合计** | **~20** | **~800-1000** |

触及此预算外文件时必须停下报告。

---

## 代码级约束（命中项）

**安全**
- 附件必须在保存前校验 MIME type（Content-Type + magic bytes 双校验，防伪造扩展名）
- 文件路径用 `<sha256>.<ext>` 命名，不使用用户提供的文件名（防路径穿越）
- `GET /attachments/{id}` 端点必须验证 `attachment.user_id == current_user.id`（防越权读取）
- pipeline 加载附件时同样校验所有权

**可靠**
- 图片 base64 上限：前端 20 MB，后端再次校验（防绕过）
- PDF 上限：32 MB
- OpenAI PDF 文本提取失败（加密/扫描件）→ 返回 `400 {"detail": "PDF 无法提取文本"}` 而非静默空字符串

**可维护**
- `AttachmentStoragePort` 接口只有 `save(data, ext, user_id) → str`、`load(attachment_id) → bytes`、`delete(attachment_id)` 三个方法，实现完全可替换

---

## 子模式展开（large — Walking Skeleton + 切片清单）

### Walking Skeleton（第一刀）

目标：端到端最薄可运行切片——上传一张 PNG，用 Anthropic profile 发一条消息，AI 能描述图片内容。

```
前端 [选文件] → POST /api/v1/attachments (multipart) → 后端保存文件 → 返回 attachment_id
→ 前端 POST /api/v1/chat/runs {attachment_ids: ["xxx"]}
→ pipeline.prepare() 从 DB 加载 AttachmentRow，解析为 AttachmentRef
→ AnthropicAdapter._convert_messages() 注入 image 内容块
→ Anthropic API 返回描述 → SSE 推流 → 前端渲染
```

完成标志：`ruff + mypy + pytest` 全绿，浏览器能看到 AI 对上传图片的描述。

### 切片清单（按依赖顺序，S 类先于 B 类）

**Slice 0 — S 类：能力标志扩展（不改行为）**
- `LLMCapabilities` 新增 `vision: bool = False`、`documents: bool = False`
- `infer_model_capabilities()` 中 claude-sonnet/opus、gpt-4o 等补充 vision/documents 标志
- 改动：`sdk/model_capabilities.py`，约 30 行

**Slice 1 — S 类：AttachmentRef domain + port（不改行为）**
- 新建 `modules/attachments/domain.py`：`AttachmentRef(id, mime_type, filename, size_bytes, storage_key)`
- 新建 `modules/attachments/ports.py`：`AttachmentStoragePort` ABC
- `ChatOptions` 新增 `attachments: list[AttachmentRef] = field(default_factory=list)`
- `ChatContext` 新增 `attachment_refs: list[AttachmentRef] = field(default_factory=list)`
- 改动：`domain/chat_options.py`、`domain/chat_context.py`，约 40 行

**Slice 2 — B 类：DB + 本地 FS adapter（Walking Skeleton 依赖此切片）**
- `infrastructure/db/models.py`：新增 `AttachmentRow`（id/user_id/filename/mime_type/size_bytes/storage_key/created_at）
- Alembic migration 文件
- `infrastructure/attachments/local_fs.py`：`LocalFSAttachmentStorage` 实现 port
- 改动：`models.py` (+20 行)；新增 migration (+20 行)；新增 `local_fs.py` (~60 行)

**Slice 3 — B 类：HTTP 上传/下载端点**
- 新建 `modules/attachments/api.py`：
  - `POST /api/v1/attachments`（multipart，返回 `{attachment_id, filename, mime_type, size_bytes}`）
  - `GET /api/v1/attachments/{id}`（流式返回文件字节，校验所有权）
  - `DELETE /api/v1/attachments/{id}`
- `factory.py` 注册路由
- 约 120 行

**Slice 4 — B 类：pipeline 附件解析（Walking Skeleton 核心）**
- `ChatPipeline.prepare()` 末尾：当 `opts.attachments` 非空时，从 DB 加载 `AttachmentRow`，读取文件字节，组装 `AttachmentRef(data=bytes)`
- `ChatRequest` 新增 `attachment_ids: list[str] = []`；`to_options()` 从 DB 加载并附到 `ChatOptions.attachments`
- HTTP `_run_chat_in_background`、`chat()` 无需改动（通过 `ChatOptions` 透传）
- 改动：`pipeline.py` (~40 行)、`chat/api.py` (~30 行)

**Slice 5 — B 类：AnthropicAdapter 多模态内容块**
- `_convert_messages` 中用户消息有 `attachment_refs` 时：将 content 改为 list，image → `{type: "image", source: {type: "base64", media_type, data}}`；PDF → `{type: "document", source: {type: "base64", media_type: "application/pdf", data}}`
- 回放时通过 `message.metadata["attachment_refs"]` 重建块（不重新读文件，只读 DB 元数据 + 文件 bytes）
- 改动：`infrastructure/llm/anthropic.py` (~60 行)

**Slice 6 — B 类：OpenAIAdapter 多模态内容块**
- 图片：content 改为 list，注入 `{type: "image_url", image_url: {url: "data:<mime>;base64,<data>"}}`
- PDF：用 `pypdf.PdfReader` 提取文本，拼接到 user content 前，加 `[PDF: <filename>]\n<text>\n---\n`；提取失败抛 `AttachmentProcessingError`
- 改动：`infrastructure/llm/openai.py` (~80 行)；`pyproject.toml` 新增 `pypdf>=4.0`

**Slice 7 — B 类：SDK `AttachmentClient` + `Conversation.send(attachments=...)`**
- `sdk/client.py`：新增 `AttachmentClient`（`upload(source: Path | bytes, filename, mime_type) → AttachmentRef`、`delete(id)`）
- `AstraCoreClient.attachments` property 返回 `AttachmentClient`
- `Conversation.send(message, attachments: list[Path | AttachmentRef] | None = None)` 自动上传 Path、直接用已有 `AttachmentRef`，附到 `ChatOptions.attachments`
- `Conversation.stream`、`stream_events` 同样接受 `attachments` 参数
- 改动：`sdk/client.py` (~120 行)

**Slice 8 — B 类：前端附件 UI**
- `features/attachments/`：`attachmentService.ts`（upload/delete API），`attachmentStore.ts`（Zustand per-message pending list）
- `ChatMain.tsx`：输入框左侧增加回形针按钮，`<input type="file" accept="image/*,.pdf">`；能力 guard：`profile.capabilities.vision === false` 时按钮禁用 + Tooltip"当前模型不支持附件"
- 附件预览缩略图（图片 blob URL；PDF 显示文件名 + 页数）
- `ChatRequest` 补充 `attachment_ids` 字段发送
- 改动：5-6 文件，约 200 行

---

## S/B 拆分顺序

```
Slice 0（S）→ 跑 mypy + pytest 绿
Slice 1（S）→ 跑 mypy + pytest 绿
Slice 2（B）→ 跑 migration + pytest（DB model）绿
Slice 3（B）→ 跑 pytest（upload/download 端点）绿
Slice 4（B）→ 跑 pytest（pipeline 加载附件）绿
Slice 5（B）→ 跑 pytest（Anthropic blocks）绿
Slice 6（B）→ 跑 pytest（OpenAI blocks + pypdf）绿
Slice 7（B）→ 跑 pytest（SDK AttachmentClient）绿
Slice 8（B）→ 浏览器端到端验证
```

每个 Slice 独立 commit，message 前缀：S 类用 `tidy:`，B 类用 `feat:`。

---

## 失败模式与验证

| # | 失败模式 | 级别 | 验证项 |
|---|---|---|---|
| F1 | 用户绕过前端限制直接 POST 超大文件（>20MB 图片/32MB PDF）→ base64 编码 OOM → 进程崩溃 | High | `test_attachment_upload_size_limit`：构造超限请求，断言返回 `413`，进程无崩溃 |
| F2 | DeepSeek/GLM profile 下 `ChatOptions.attachments` 非空 → `pipeline.prepare()` 未检查能力 → OpenAI adapter 收到 image blocks → API 500 | High | `test_pipeline_prepare_raises_on_vision_incapable`：mock OpenAI adapter，断言 `pipeline.prepare()` 对无 `vision` capability profile 抛 `AttachmentCapabilityError(400)` |
| F3 | 加密/扫描 PDF 提取文本为空 → OpenAI adapter 把空字符串注入 prompt → AI 静默返回"无法获取内容" | High | `test_openai_pdf_extraction_failure`：传入加密 PDF bytes，断言抛 `AttachmentProcessingError`，HTTP 层返回 `400 {"detail": "PDF 无法提取文本"}` |
| F4 | user_A 上传的附件 attachment_id 被 user_B 引用（IDOR）→ user_B 读到 user_A 文件内容 | High | `test_attachment_cross_user_access`：以 user_B token 请求 user_A attachment，断言 `403`；pipeline 加载时同样做所有权断言 |
| F5 | 历史消息中 `ChatRunRow.attachment_ids` 引用的文件被删除 → 回放时读文件抛 `FileNotFoundError` → 500 | Med | `test_attachment_missing_on_replay`：删除文件后重建短期记忆，断言用户消息 content 包含 `[附件已删除: <filename>]` 占位符而非崩溃 |
| F6 | `_rebuild_short_term_from_runs` 重建历史时未携带 attachment_refs → 下一轮 AI 看不到附件 | Med | `test_rebuild_short_term_preserves_attachments`：upload → send → delete session cache → rebuild，断言重建后消息含 attachment_refs metadata |

---

## 推荐与决策

**推荐方案**：上文完整方案（本地 FS + port 抽象 + 跨协议适配 + SDK 对等 + 前端能力 guard）。

**Decision Drivers 评分**

| Driver | 权重 | 本方案 | 只做 Anthropic | 无 port 抽象 |
|---|---|---|---|---|
| 专业可用性 | 高 | ★★★ | ★★ | ★★★ |
| SDK 对等 | 高 | ★★★ | ★★★ | ★★ |
| 跨协议适配 | 高 | ★★★ | ★ | ★★★ |
| 代码可维护 | 中 | ★★★ | ★★★ | ★★ |
| 首版实现成本 | 中 | ★★ | ★★★ | ★★★ |

**为什么不只做 Anthropic**：项目配置文件里有 DeepSeek/GLM profile，用户明确要求"专业好用"，跨协议是基本要求。

**为什么不去掉 port 抽象**：项目架构风格就是 ports/adapters（`shared/ports/llm.py` 同款），去掉会破坏一致性，且本地 FS 在生产环境不可扩展。port 接口只有三个方法，成本极低。

**影响范围**：跨模块（DB/storage/LLM adapters/pipeline/HTTP/SDK/前端），改公开接口（`ChatOptions`、`LLMCapabilities`）。

**下一步实施边界**  
building 从 Slice 0 开始，按切片顺序逐个实施。每个 Slice 完成后运行 `make check`（ruff + mypy）+ 对应 pytest 用例。触及 Non-Goals / 超 diff 预算 / 越切片边界时停下回 planning 重评估。

**重评估条件**
- `pypdf` 提取成功率低于预期 → 考虑改用 `pdfminer.six` 或 pymupdf
- Anthropic Files API 稳定后 → Slice 5 可升级为先上传文件取 `file_id`，减少每次请求的 base64 体积
- 用户需求扩展到 Office 文档 → 需要新 `DocumentExtractor` port

---

## 决策记录

**日期**：2026-06-24  
**上下文与问题**：用户要求支持图片+PDF附件，跨 Anthropic/OpenAI 兼容协议，SDK 与 HTTP 功能对等，不考虑成本，必须专业好用。  
**Decision Drivers**：跨协议适配、SDK 对等、专业可用性、ports/adapters 架构一致性。  
**决策结果**：本地 FS + `AttachmentStoragePort` 抽象，`LLMCapabilities` 增加 vision/documents，pipeline 统一解析附件，Anthropic 走原生 content blocks，OpenAI 走 image_url + pypdf，SDK 新增 `AttachmentClient` + `Conversation.send(attachments=...)`，前端能力 guard 在 capabilities.vision=false 时禁用上传。  
**正面后果**：SDK/HTTP 用户体验一致；换 S3 只需换 LocalFSAttachmentStorage；LLM 协议细节封装在各 adapter 内。  
**负面后果**：首版约 800-1000 行改动，比只做 Anthropic 重约一倍；pypdf 在扫描件上失败率不确定。  
**重评估条件**：见上文"重评估条件"。
