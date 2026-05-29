---
title: AstraCoreAI 技能系统（Skills）
category: astracore
tags: [Skill, 技能路由, 向量路由, 系统提示, anchor, routed]
related: [astracore/intro, astracore/chat_pipeline]
---

# AstraCoreAI 技能系统（Skills）

**技能（Skill）** 是 AstraCoreAI 中可配置的 AI 能力包，每个技能包含专属的系统提示、工具权限和参考资料。

## 技能结构

每个内置技能对应 `modules/skills/builtin/<skill-name>/` 目录：

```
<skill-name>/
├── SKILL.md          # 必须，包含 frontmatter + 系统提示内容
└── <ref>.md          # 可选，参考资料文档（LLM 按需加载）
```

SKILL.md frontmatter 字段：

| 字段 | 说明 |
|------|------|
| `name` | 技能显示名称（必填） |
| `description` | 简短描述，用于路由匹配 |
| `order` | 排序值，越小越靠前 |
| `default` | true 表示首次启动时设为默认 |
| `references` | 参考资料列表（title/description/file） |

## 内置技能

| 技能 | 定位 |
|------|------|
| assistant | 通用助手（默认） |
| programmer | 代码开发助手 |
| translator | 翻译专家 |
| writer | 写作助手 |
| novel-writer | 网文创作（含参考库） |
| analyst | 数据分析师 |
| storyteller | 故事创作 |
| financial-advisor | 财务顾问 |
| openclaw-manager | OpenClaw 管理器 |

## 自动路由

技能路由支持三种模式（`skill_routing.mode` 配置）：

### vector 模式（推荐）
- 启动时对所有技能的 `name+description` 预计算 sentence-transformers 嵌入
- 查询时对用户消息做嵌入，余弦相似度排序
- 超过 `threshold`（默认 0.45）才触发，最多匹配 `max_skills` 个

### llm 模式
- 构造技能列表上下文，由轻量 LLM 返回匹配的 skill ID 列表
- 精度更高，但消耗额外 LLM token

### off 模式
- 完全手动选择，不自动路由

## 主/副技能

路由结果分为：
- **主技能（📌 anchor）**：用户手动选择，完整注入系统提示
- **副技能（⚡ routed）**：自动路由命中，以名称+描述 bullet list 形式补充注入

## 参考资料访问

技能关联的参考文档通过内置工具 `get_skill_reference()` 按需加载，LLM 可在需要时主动调用获取完整内容。

## 扩展技能

在 `config.yaml` 中配置 `skills.extra_dirs` 指向自定义目录，重启后自动加载。
