# Skill 系统重设计方案

> 版本：v1.0 · 日期：2026-06-01  
> 状态：已实施 ✅（2026-06-01 完成，见进度规划 WP-26）

---

## 一、背景与现状问题

### 当前设计

AstraCoreAI 的 Skill 系统本质上是**命名 System Prompt 注入机制**：用户（或服务端路由）选定一个 Skill，后端将其 `system_prompt` 字段拼入对话的 system prompt，Claude 按此角色设定回复。

### 存在的根本性问题

**1. 概念混用：角色 ≠ 能力**

现有 Skill 把两个不同概念混在一张表里：
- **角色（Persona）**：定义 Claude 是谁，语气风格（通用助手、代码助手）
- **能力（Capability）**：定义 Claude 能做什么，操作步骤、参考知识、可执行脚本

这导致用户只能"选一个"，无法在一次对话里组合多个专业能力。

**2. 服务端路由是反模式**

```
用户消息 → 服务端向量/LLM 路由 → 猜测最匹配的 Skill → 注入 System Prompt → Claude 回复
                                    ↑
                           Claude 完全看不到这个决策过程
```

Claude 自己有完整的上下文、理解意图的能力，完全可以比服务端路由做出更好的判断。用机器学习模型去模仿 Claude 的推理，是多余的一层。

**3. 跨产品不兼容**

用户自建 Skill 锁在数据库里，无法与 Claude Code、Cursor、GitHub Copilot 等支持 Agent Skills 开放标准的工具互通。

**4. 脚本支持不完整**

已有 `run_command` 工具和 `{{skill_dir}}` 占位符，但 Skill 目录里的脚本没有规范的发现和调用机制。

---

## 二、设计目标

1. **Skills = 能力包**：Skill 是 Claude 可以按需加载的专业能力，而非预设角色
2. **Claude 主导决策**：由 Claude 自主判断何时加载哪些 Skill，而非服务端路由
3. **兼容 Agent Skills 开放标准**：格式与 [agentskills.io](https://agentskills.io) 规范完全兼容
4. **支持脚本执行**：Skill 可以打包可执行脚本，Claude 调用时在沙箱内运行
5. **渐进式披露**：Skill 元数据始终在 context 里，完整内容按需加载，控制 token 消耗

---

## 三、核心概念重定义

### 3.1 身份层（Identity Layer）—— 始终在 System Prompt

定义这个 AI 是谁，始终注入，不受 Skill 影响：

```
ai_name / owner_name / 基础人格 / 输出规范 / 全局指令
```

由用户在 Settings 页面配置（现有的 `global_instruction` + 个人信息字段）。

### 3.2 Skill = 能力包（Capability Package）

Skill 不再定义"角色"，而是定义"能力"：

- 专业领域的操作步骤
- 可引用的参考文档
- 可执行的脚本工具
- 特定任务的 checklist、模板

Claude 在 System Prompt 中看到所有 Skill 的**摘要清单**，根据任务上下文自主决定加载哪些。

### 3.3 废弃服务端 Skill Routing

移除 `skill_routing` 模块（vector 模式和 llm 模式）。Claude 通过 `load_skill` 工具自主完成路由，且路由精度更高。

---

## 四、Skill 格式规范

完全兼容 [Agent Skills 开放标准](https://agentskills.io/specification)。

### 4.1 目录结构

```
skill-name/           ← 目录名必须与 SKILL.md 中的 name 字段一致
├── SKILL.md          ← 必须：元数据 + 指令正文
├── scripts/          ← 可选：可执行脚本
│   ├── format.py
│   └── validate.sh
├── references/       ← 可选：参考文档（按需加载）
│   ├── style-guide.md
│   └── examples.md
└── assets/           ← 可选：模板、静态资源
    └── template.md
```

### 4.2 SKILL.md 格式

```markdown
---
name: novel-writing                              # 必填，小写字母/数字/连字符，与目录名一致
description: |                                   # 必填，描述"做什么"和"何时使用"
  网络小说写作能力包。适用于续写章节、设计情节、
  塑造角色、分析文风等任务。
license: Proprietary                             # 可选
compatibility: Requires AstraCoreAI >= 2.0       # 可选，环境要求
metadata:                                        # 可选，扩展元数据
  author: system
  version: "1.0"
  category: writing
allowed-tools: Bash(python:*) Read               # 可选，预授权工具（实验性）
---

## 能力说明

本 Skill 提供网络小说写作的专业支持，包括：...

## 操作步骤

1. 首先读取上下文（已有章节、人物设定）
2. 分析当前情节节点...

## 参考资源

详细写作技巧见 [references/style-guide.md](references/style-guide.md)。
情节框架模板见 [references/plot-framework.md](references/plot-framework.md)。

## 可用脚本

- `scripts/format_chapter.py`：将章节格式化为标准排版
- `scripts/word_count.py`：统计字数并生成报告
```

### 4.3 与开放标准的差异（AstraCoreAI 扩展）

在标准字段之外，AstraCoreAI 在 `metadata` 中使用以下约定字段：

```yaml
metadata:
  order: "10"          # 清单展示顺序（数字字符串，越小越靠前）
  category: writing    # 分类标签，用于前端分组展示
  builtin: "true"      # 标记为内置 Skill，不可删除
```

这些字段通过标准的 `metadata` 扩展机制实现，不破坏跨产品兼容性。

---

## 五、系统架构

### 5.1 三层 System Prompt 结构

```
┌─────────────────────────────────────────────────────┐
│  Layer 1：身份层（Identity）                          │
│  ai_name / owner_name / 性格 / 输出规范 / 全局指令    │  ← 始终注入
├─────────────────────────────────────────────────────┤
│  Layer 2：Skill 摘要清单（Manifest）                  │
│  所有可用 Skill 的 name + description 摘要            │  ← 始终注入
│  ~100 tokens per skill                               │
├─────────────────────────────────────────────────────┤
│  Layer 3：动态上下文（Dynamic Context）               │
│  RAG 检索结果 / 记忆引擎内容                          │  ← 按需注入
└─────────────────────────────────────────────────────┘
```

### 5.2 System Prompt 中的 Skill 清单格式

```markdown
## 可用技能

你可以通过 `load_skill` 工具按需加载以下技能的完整说明。
加载后，按技能的指令执行任务。

| ID | 技能名称 | 描述 |
|----|----------|------|
| novel-writing | 网络小说写作 | 续写章节、情节设计、角色塑造 |
| code-review | 代码审查 | Python/TypeScript 代码规范检查和重构建议 |
| data-analysis | 数据分析 | 数据清洗、统计分析、可视化方案 |
| ...  | ...  | ... |
```

### 5.3 完整请求处理流程

```
用户发送消息
    │
    ▼
后端组装 System Prompt
    ├─ 身份层（ai_name, global_instruction 等）
    └─ Skill 摘要清单（所有 Skill 的 name + description）
    │
    ▼
发送给 Claude（含工具：load_skill, get_skill_reference, run_skill_script）
    │
    ▼
Claude 分析任务
    ├─ 判断是否需要加载 Skill
    ├─ 调用 load_skill("novel-writing") → 获取完整指令
    ├─ 调用 get_skill_reference("novel-writing", "style-guide.md") → 获取参考文档
    └─ 调用 run_skill_script("novel-writing", "format_chapter.py", {...}) → 执行脚本
    │
    ▼
Claude 综合所有信息，生成回复
```

---

## 六、工具接口设计

### 6.1 `load_skill`

加载指定 Skill 的完整 SKILL.md 指令正文。

**输入**
```json
{
  "skill_id": "novel-writing"
}
```

**输出**
```json
{
  "skill_id": "novel-writing",
  "name": "网络小说写作",
  "instructions": "## 能力说明\n\n本 Skill 提供...",
  "references": [
    { "file": "references/style-guide.md", "description": "写作技巧参考" },
    { "file": "references/plot-framework.md", "description": "情节框架模板" }
  ],
  "scripts": [
    { "name": "format_chapter.py", "description": "格式化章节排版" },
    { "name": "word_count.py", "description": "字数统计" }
  ]
}
```

### 6.2 `get_skill_reference`

加载 Skill 内的参考文档（现有工具，调整参数）。

**输入**
```json
{
  "skill_id": "novel-writing",
  "file": "references/style-guide.md"
}
```

**输出**：文档内容（Markdown 字符串）

### 6.3 `run_skill_script`（新增）

在沙箱内执行 Skill 的附属脚本。

**输入**
```json
{
  "skill_id": "novel-writing",
  "script": "format_chapter.py",
  "args": {
    "input_text": "第五十六章 ...",
    "style": "standard"
  }
}
```

**输出**
```json
{
  "exit_code": 0,
  "stdout": "格式化完成，共 3200 字",
  "stderr": "",
  "duration_ms": 245
}
```

**安全约束**
- 脚本路径限制在 `{skill_dir}/scripts/` 内，防止路径穿越
- 执行超时：默认 30s，Skill 可通过 `metadata.script_timeout` 覆盖
- 网络访问：默认禁止，`compatibility` 字段声明需要后可开启

### 6.4 `list_skills`（可选，用于调试）

返回所有可用 Skill 的摘要，供 Claude 在不确定时主动查询。

---

## 七、内置 Skill 迁移方案

### 7.1 角色型 Skill 的处置

现有内置 Skill（通用助手、代码助手、写作助手等）的 `system_prompt` 大部分是角色设定（人格、输出规范），这些内容应迁移至**身份层**，而非保留为 Skill。

迁移策略：

| 现有 Skill | 迁移方向 |
|-----------|---------|
| 通用助手 | 提炼为默认 `global_instruction` 模板 |
| 代码助手（人格部分） | 合并入身份层 |
| 代码助手（规范部分）| 转为 `code-review` 能力 Skill |
| 小说写作大师（人格）| 合并入身份层可配置项 |
| 小说写作大师（技法）| 转为 `novel-writing` 能力 Skill |
| 分析师 | 转为 `data-analysis` 能力 Skill |
| 译者 | 转为 `translation` 能力 Skill |

### 7.2 数据库迁移

1. `skills` 表保留，`is_builtin` 字段保留
2. 新增字段 `skill_type: enum('capability', 'legacy_persona')`，迁移期标记旧数据
3. 旧的 `system_prompt` 字段保留但逐步废弃，新 Skill 使用 SKILL.md 文件
4. `sort_order` → 迁移至 SKILL.md 的 `metadata.order`

### 7.3 Skill Routing 模块

`src/astracore/modules/skills/router.py` 整个模块废弃删除。

---

## 八、用户自建 Skill

### 8.1 两种创建方式

**方式 A：UI 编辑器（简单模式）**

用户通过前端表单创建，存储为数据库记录。字段：
- `name`（kebab-case，作为 skill_id）
- `description`（不超过 1024 字符）
- `instructions`（SKILL.md 正文，Markdown）
- `category`（分类标签）

没有脚本和参考文档支持，适合大多数用户。

**方式 B：文件系统目录（高级模式）**

在 `config.yaml` 中配置额外 Skill 目录：

```yaml
skills:
  extra_dirs:
    - ~/my-skills
    - /team/shared-skills
```

启动时自动扫描，支持完整的 `scripts/`、`references/`、`assets/` 结构。

### 8.2 导出为标准格式

UI 创建的 Skill 支持一键导出为标准 Agent Skills 目录（zip），可直接在 Claude Code、Cursor 等工具中使用。

---

## 九、前端变化

### 9.1 Skills 页面重构

| 现在 | 新设计 |
|------|--------|
| 列表展示 name / description / system_prompt | 列表展示 name / description / category |
| 编辑 system_prompt 文本框 | 编辑 instructions（SKILL.md 正文） |
| 无脚本管理 | 显示脚本列表（高级 Skill） |
| 无导出 | 导出为标准格式按钮 |

### 9.2 对话中的 Skill 交互

现有 `SkillSelector` 下拉框语义变更：

| 现在 | 新设计 |
|------|--------|
| 选择注入哪个 Skill 的 system_prompt | 置顶/优先推荐某个 Skill（可选） |
| 决定角色 | 提示 Claude 优先关注某个能力方向 |

当用户在 SkillSelector 中选择一个 Skill 时，System Prompt 的清单里该 Skill 会被标记为 `[置顶]`，Claude 会优先考虑加载，但仍可按需组合其他 Skill。

### 9.3 新增：Skill 加载状态可视化

在消息流中显示 Claude 加载了哪些 Skill（类似现有 Tool Activity 的展示）：

```
✦ 加载技能：novel-writing · style-guide.md
```

---

## 十、后端变化

### 10.1 废弃

- `src/astracore/modules/skills/router.py`（整个模块）
- `SkillRow.system_prompt` 字段（迁移期保留，后续版本删除）
- `_build_system_prompt()` 中的 Skill 注入逻辑（改为 Manifest 注入）

### 10.2 新增

- `SkillService.get_skill_manifest()` → 返回所有 Skill 的 name + description 清单
- `SkillService.load_skill(skill_id)` → 返回完整 Skill 内容（instructions + references list + scripts list）
- `SkillService.get_skill_reference(skill_id, file)` → 返回参考文档内容（现有，接口调整）
- `SkillService.run_skill_script(skill_id, script, args)` → 在沙箱执行脚本（新增）
- `SkillRow.instructions` 字段（Markdown 正文，对应 SKILL.md body）

### 10.3 Chat Pipeline 变化

```python
# 现在
system_prompt = build_skill_prompt(selected_skill) + global_instruction + rag_context

# 新设计
system_prompt = build_identity_layer() + build_skill_manifest() + rag_context
# Skill 内容由 Claude 通过 load_skill 工具按需拉取，不在 system prompt 里
```

---

## 十一、未解决问题（Open Questions）

| 问题 | 背景 | 待决策 |
|------|------|--------|
| **用户还需要"角色"选择吗？** | 新设计里 Skill 全是能力，AI 的人格由身份层决定。部分用户可能还想"切换成代码助手模式" | 是否保留一个轻量的 Persona 选择功能？ |
| **Skill 清单 token 上限** | 如果有 50 个 Skill，每个 description 100 字符，清单本身就有约 1500 tokens | 是否需要分类折叠或分页加载清单？ |
| **脚本沙箱实现** | `run_skill_script` 需要安全边界，防止脚本越权 | 用 Docker 容器还是 Python subprocess + chroot？ |
| **UI 创建的 Skill 无法打包脚本** | 文件上传 UI 增加复杂度 | 是否支持在 UI 里上传脚本文件？ |
| **现有会话的 Skill 引用** | 数据库里老消息关联了旧格式的 `skill_id` | 迁移后这些 skill_id 是否还能解析？ |

---

## 十二、参考资料

- [Agent Skills 开放标准规范](https://agentskills.io/specification)
- [Anthropic 工程博客：Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Claude Code Skill 文档](https://code.claude.com/docs/en/skills)
- 当前实现：`src/astracore/modules/skills/`
