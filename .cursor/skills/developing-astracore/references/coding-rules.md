# 编码约定与审美

> TOC：硬约定 · 安全默认 · 抽象标准 · 注释 · 删除纪律

## 1. 硬约定（违反即回改）

| 规则 | 做法 |
|---|---|
| Import 位置 | **文件顶部**；禁止函数内 `import`（仅允许 redis 客户端一类与 HybridMemory 同风格的 lazy 可选依赖） |
| TODO / 占位 | **禁止**交付含 TODO、`...`、`implement here`、`其余保持不变` |
| 向前兼容 | 未发生产：直接改最优形态，删死代码，不留兼容分支 |
| 注释语言 | 新增注释用**中文**；保留已有有意义注释，不整段清空 |
| 代码/日志语言 | 标识符与日志消息用英文；用户可见 HITL 文案可用中文 |
| 业务真相 | 只在 `modules/`；router / sdk 只做适配 |

## 2. 安全默认（fail-closed）

- 未知工具：不可并发、非只读  
- 缺 HITL callback：确认类工具拒绝，不静默执行  
- 外部内容：`wrap_external`  
- 破坏性操作：`is_destructive=True`；可选 `soft_exec` 预览  

写「默认允许、出问题再收」= 审美不合格。

## 3. 何时抽象（够了再抽）

同时满足再抽层：

1. 同样逻辑 ≥ 3 处，或  
2. 业务上已有名字（如 Toolset、TurnBudget、LLMRoundStrategy），或  
3. 已有 ≥ 2 个真实变种（流式/非流式策略）

禁止为「将来可能」预留空基类、空中间件、空 Plugin 总线。

参数对象优先于长参数列表：参考 `ToolLoopConfig`、`ChatOptions`、`ChatContext`。

## 4. 函数 / 文件体感

| 信号 | 动作 |
|---|---|
| 函数 > 40 行且多层分支 | 拆私有方法或策略对象 |
| 文件核心编排 > 400 行持续膨胀 | 抽 `application/` 协作类（attachment_loader / history / llm_factory 模式） |
| 流式与非流式两套 while | **合并**（见 agent-loop） |
| 复制粘贴改两个入口 | 抽共用函数，立刻删副本 |

## 5. 注释写什么

写：非显然的业务约束、fail-closed 原因、与外部协议的坑  
不写：复述代码的叙事、改动日志式注释、被删代码的墓碑

## 6. 删除纪律

- 确认失效的逻辑直接删，不注释保留  
- 迁移后旧入口删除或改成薄委托（测试依赖的别名可保留一行，如 `_prepare_for_save = prepare_for_save`）  
- 公共 API 改名：同步测试与 SDK/HTTP，不留双路径「过渡期」
