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

## 参考资料访问

技能关联的参考文档通过内置工具 `get_skill_reference()` 按需加载，LLM 可在需要时主动调用获取完整内容。

## 扩展技能

在 `config.yaml` 中配置 `skills.extra_dirs` 指向自定义目录，重启后自动加载。
