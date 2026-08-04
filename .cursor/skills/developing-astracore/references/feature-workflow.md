# 功能 / 修复落地流程

> TOC：分层顺序 · ChatOptions · 新工具 · 新模块 · 前端联动 · 失败模式

## 1. 分层落地顺序（强制）

```
1. modules/<feature>/     # domain + application（唯一业务真相）
2. HTTP API               # Pydantic schema + router，调用 service
3. SDK                    # AstraCoreClient / 子 Client，同样调用 service
4. 测试                   # 单测覆盖根因路径；集成测覆盖对等入口
5. 前端（若有 UI）         # features/<name>/ 镜像后端概念
```

禁止：业务写在 `api.py` 或 `sdk/client.py` 里再「以后抽」。

## 2. 改一轮对话选项

1. 在 `modules/chat/domain/chat_options.py` 增加字段（带默认值与中文 docstring）  
2. 同步 `ChatContext`（若 prepare/stream 需要）  
3. `ChatPipeline.prepare()` 写入 context  
4. HTTP：`ChatRequest` + `to_options()`  
5. SDK：`chat` / `chat_stream` 关键字参数 **或** 文档化 `options=ChatOptions(...)`  
6. 测试：至少一条 options → context 断言  

现成例子：`toolset`、`soft_exec`、`max_input_tokens`、`max_output_tokens`。

## 3. 新增 Builtin 工具

1. 在 `modules/tools/builtin.py`（或 skills 工具注册点）`register_tool`  
2. 填齐安全字段：`is_concurrency_safe` / `is_readonly` / `is_destructive` / `requires_confirmation`  
3. 需要会话上下文时声明 `_context` 参数（框架注入 session_id、user_id、llm_adapter、hitl_callback）  
4. 如需裁剪：更新 `toolset.py` 对应集合  
5. 单测：Schema 失败回流、destructive+soft_exec 跳过、分区行为（若涉及 path）  

## 4. 新增业务模块（最小骨架）

```
modules/<name>/
  domain/          # 纯模型，无 I/O
  application/     # 用例 / service
  ports/           # 仅本模块需要的抽象（或复用 shared/ports）
  api.py           # FastAPI router（薄）
```

装配：`app/factory.py` 挂路由；SDK 增加薄 facade 属性。  
Infra 适配器放 `infrastructure/<name>/`，由工厂注入。

## 5. 修 Agent / 工具相关 bug

1. 复现：写失败测试（优先 RED）  
2. 定位层：是 Schema / HITL / partition / compact / transcript / budget？  
3. 修根因所在模块，禁止在 API 层 `if` 补丁  
4. 确认流式与非流式都走同一 `_run_loop`（改一处即可）  

## 6. 前端联动检查清单

当后端新增 SSE 事件或 run 字段时：

- [ ] `features/chat` 事件处理是否识别新 `event`  
- [ ] 控件是否仍由 `controls` / capabilities 驱动（能描述驱动就别硬编码 model 名）  
- [ ] 401 拦截与 Bearer 注入未被破坏  

纯后端能力无 UI 时，在交付说明写「前端无改」。

## 7. 高频失败模式（先防再写）

| ID | 症状 | 根因倾向 | 动作 |
|---|---|---|---|
| F1 | 写工具互相覆盖 | 未分区 / 未标 unsafe | 查 `partition` + 工具安全字段 |
| F2 | 长对话丢摘要 | compact 被 prepare_for_save 滤掉 | 查 `compacted=True` 放行 |
| F3 | Schema 过严弄死 MCP | additionalProperties 拒绝 | 对照 `validate.py` 策略 |
| F4 | Toolset 砍掉 ask_user | default 集合漏核心工具 | 跑 toolset 快照测试 |
| F5 | short-term 空会话失忆 | 未 replay transcript | 查 `load_history` |
| F6 | 只改了 API | SDK 参数缺失 | 对等检查表（见 verification） |
