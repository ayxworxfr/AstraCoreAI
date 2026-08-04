---
name: developing-astracore
description: >-
  Guides feature work, bugfixes, and refactors inside the AstraCoreAI monorepo
  (FastAPI + React AI assistant with shared ChatPipeline for SDK and HTTP).
  USE WHEN developing AstraCoreAI, changing chat pipeline, tool loop, tools,
  memory, RAG, scheduling, SDK/API parity, RunRegistry, transcript, or when the
  user says 按项目风格改、补 SDK/API、加工具、改 pipeline.
  Do not use for unrelated repositories, generic Python tutoring, or non-code tasks.
---

# Developing AstraCore

## Core Principle

AstraCore 的正确开发姿态是：**业务只进 `modules/`，HTTP 与 SDK 是对称门面，Agent 循环只保留一条编排脊柱。**

先定位层（domain / application / infra / api / sdk），再改代码。复制一套流式循环、把逻辑写进 router、只改 API 不改 SDK——这三条是最高频的毁项目方式，本 skill 把它们堵死。

本 skill **自包含**：架构与约定写在 `references/`，不依赖仓库外文档，也不要求先读 `docs/`。

## 子模式判定

| 用户信号 | 子模式 | 加载 |
|---|---|---|
| 新增能力 / 补字段 / 加工具 / 加模块 | **feature** | [feature-workflow.md](references/feature-workflow.md) |
| 报错 / 行为不对 / 修 bug / 回归 | **fix** | [feature-workflow.md](references/feature-workflow.md) §5 + [agent-loop.md](references/agent-loop.md) |
| 拆文件 / 去重 / 统一循环 / 抽参数对象 | **refactor** | [coding-rules.md](references/coding-rules.md) + [agent-loop.md](references/agent-loop.md) |

不确定时：先读 [architecture.md](references/architecture.md) 定位模块，再选子模式。

## 强制流程

### 1. 定位

搜索相关符号与目录 → 对照 [architecture.md](references/architecture.md) 关键路径表 → 写下「改哪些文件、不动哪些层」。

触及工具循环 / transcript / run SSE 时，继续读 [agent-loop.md](references/agent-loop.md)。

### 2. 选层与对等面

列出本次变更落在：

- [ ] `modules/` 业务  
- [ ] HTTP API  
- [ ] SDK  
- [ ] tests  
- [ ] frontend（仅 UI 需要时）

**feature 缺 SDK 或 HTTP 任一侧 → 未完成。** 细则见 [feature-workflow.md](references/feature-workflow.md)。

### 3. 读现有模式

打开同目录最近似实现（工具注册、Options 字段、service 方法）→ 复制**结构**而非复制过时逻辑 → 对照 [coding-rules.md](references/coding-rules.md) 的 fail-closed 与抽象标准。

### 4. 实施

按子模式执行：

- **feature**：domain → application → API → SDK → 测试  
- **fix**：先失败测试或最小复现 → 修根因层 → 删症状补丁  
- **refactor**：行为不变优先；流式/非流式合并到 `_run_loop`；大文件拆进 `application/`

中段提醒（D3）：写到一半若开始在 `api.py` 堆业务或复制 `execute_*_with_tools`，停下来回到步骤 2。

### 5. 验证

按 [verification.md](references/verification.md) 跑命令与对等表 → 用命令输出作证据 → 再声称完成。

## 非协商约束（摘要）

完整细则在 references；此处只列必须始终记住的：

1. Import 置顶；无 TODO / 占位交付  
2. 业务不进 router / `sdk/client.py`  
3. Agent 循环单一编排；策略只换 LLMRound  
4. 工具安全字段显式声明；默认 fail-closed  
5. Compact 摘要必须带 `compacted=True` 且能回注  
6. 外部内容 `wrap_external`  
7. 完成声明必须有测试/检查命令证据  

## 硬阻断（输出前）

| # | 命中即停 | 替代动作 |
|---|---|---|
| H1 | 只改了 HTTP 或只改了 SDK | 补另一侧 + 对等检查表 |
| H2 | 新增第二套 tool while/stream 循环 | 合并进 `_run_loop` + 策略对象 |
| H3 | 业务逻辑写在 `api.py` / `client.py` | 下沉到 `modules/*/application` |
| H4 | 工具未标 `is_concurrency_safe` / destructive 语义 | 按 [agent-loop.md](references/agent-loop.md) §3 补齐 |
| H5 | 无验证证据声称「好了」 | 跑 [verification.md](references/verification.md) 命令并贴结果 |
| H6 | 交付含 TODO / 占位 /「其余不变」 | 写完可运行实现或缩小范围并明示未做项 |

## 禁止输出

| 别说 | 改做 |
|---|---|
| 「SDK 应该也能用」 | 指出方法签名，或补上参数并跑导入/测试 |
| 「先改 API 以后再补 SDK」 | 同 PR / 同会话内两侧落地 |
| 「流式单独写一套更清晰」 | 读 `llm_round.py`，扩策略而非复制循环 |
| 「默认并行没事」 | 查分区与工具安全字段 |
| 「应该没问题 / 基本对齐」 | 贴 pytest / ruff / mypy 证据 |
| 「其余代码保持不变」 | 给出完整可运行改动或明确文件级 diff 范围 |

## 输出骨架

```markdown
## 子模式
- feature | fix | refactor

## 变更范围
- 模块：
- HTTP：
- SDK：
- 前端：无 / 有（文件列表）

## 设计要点
- 为何落在这一层：
- 对等如何保证：
- 安全默认（并发 / HITL / wrap）：

## 验证
- 命令：
- 结果：

## 残留
- 无 | 未做项 + 原因
```

## References

| 文档 | 何时读 |
|---|---|
| [architecture.md](references/architecture.md) | 定位模块 / 数据流 / 双表面 |
| [agent-loop.md](references/agent-loop.md) | 工具循环、分区、transcript、预算、RunRegistry |
| [feature-workflow.md](references/feature-workflow.md) | 加功能、加工具、修 Agent bug |
| [coding-rules.md](references/coding-rules.md) | 风格、抽象、删除纪律 |
| [verification.md](references/verification.md) | 交付前命令与对账 |
