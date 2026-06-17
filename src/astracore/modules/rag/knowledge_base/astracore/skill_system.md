---
title: AstraCoreAI 技能系统（Skills）
category: astracore
tags: [Skill, Agent Skills, load_skill, 三层SystemPrompt, Claude路由]
related: [astracore/intro, astracore/chat_pipeline, astracore/tool_system]
---

# AstraCoreAI 技能系统（Skills）

**技能（Skill）** 是 Claude 可按需加载的专业能力包，而非预注入的 System Prompt 角色。格式兼容 [Agent Skills 开放标准](https://agentskills.io)。

## SKILL.md 格式

每个内置技能对应 `modules/skills/builtin/<skill-name>/` 目录：

```
<skill-name>/
├── SKILL.md          # 必须，frontmatter + 正文 instructions
├── references/       # 可选，参考资料文档（Claude 按需加载）
│   └── *.md
└── scripts/          # 可选，可执行脚本
    └── *.py
```

SKILL.md frontmatter 字段：

| 字段 | 说明 |
|------|------|
| `name` | Skill ID（kebab-case，与目录名一致） |
| `description` | 做什么 + 何时使用，Claude 据此决定是否加载 |
| `metadata.display_name` | 前端展示名称 |
| `metadata.order` | 排序值，越小越靠前 |
| `metadata.category` | 分类标签（general / coding / writing / analysis 等） |

示例：

```yaml
---
name: assistant
description: |
  日常问答、技术解释、方案对比、任务拆解。适用于通用助手场景。
metadata:
  display_name: "通用助手"
  order: "10"
  category: "general"
---
## 正文 instructions...
```

## 三层 System Prompt

每次对话 System Prompt 由三层拼接：

| 层 | 内容 | 注入时机 |
|----|------|---------|
| **身份层** | ai_name、owner_name、当前时间、global_instruction | 始终注入 |
| **Skill 摘要清单** | 所有 Skill 按 category 分组，每条含 name + description | 始终注入 |
| **动态上下文** | RAG 召回结果 + 记忆引擎内容 | 按需注入 |

Claude 读取 Skill 摘要清单后自主决策何时调用哪个 Skill，无需服务端路由。

## 三个 Skill 工具

| 工具 | 作用 |
|------|------|
| `load_skill(skill_id)` | 加载 Skill 完整 instructions、references 列表、scripts 列表 |
| `get_skill_reference(skill_id, file)` | 读取 references/ 目录下指定文档的完整内容 |
| `run_skill_script(skill_id, script, args)` | 在 scripts/ 目录内执行脚本（防路径穿越，30s 超时） |

工具循环始终激活（`needs_tool_loop = True`），Claude 可在任意轮次调用上述工具。

## 内置技能

| 技能 ID | 显示名 | 分类 |
|---------|--------|------|
| `assistant` | 通用助手 | general |
| `programmer` | 代码助手 | coding |
| `analyst` | 数据分析师 | analysis |
| `writer` | 写作助手 | writing |
| `novel-writer` | 小说写作大师 | writing |
| `storyteller` | 故事大师 | writing |
| `translator` | 翻译官 | language |
| `financial-advisor` | 理财顾问 | finance |
| `mini-game` | 小游戏主持人 | game |
| `openclaw-manager` | OpenClaw 管理员 | ops |

## 扩展技能

在 `config.yaml` 中配置 `skills.extra_dirs` 指向自定义目录，重启后自动加载：

```yaml
skills:
  extra_dirs:
    - D:/my-skills
    - ~/shared-skills
```

同名 `source_key`（目录名）时，后配置的目录覆盖先配置的目录并输出警告日志。
