# Skills 设计文档

本目录存放 Astra AI 的 skill 配置。每个 skill 是一份**完整的人格 + 工作手册**，通过互斥加载在对话中生效。

本文档既是**设计哲学说明**，也是**新增 skill 时的操作模板**。

---

## 一、加载机制

- **互斥加载**：每次对话只激活一个 skill，由 `description` 语义匹配决定
- **order 字段**：数字越小越靠前，多 skill 同时命中时优先激活 order 值更小的
- **description 字段**：触发匹配的核心——见下一节
- 互斥加载意味着**每个 skill 必须自带所有必要的基础规则**（人格、沟通方式、格式规范），不能假设"通用助手另外生效"

---

## 二、description 写法

> description 是 AI 判断"该不该激活这个 skill"的唯一依据。写得模糊 = skill 永远不会被主动加载。

### 三段式模板

```
[Skill 名称]。[能力范围一句话]。USE WHEN [具体触发场景/关键词/用户意图]。DO NOT USE for [明确排除项，并指向替代 skill]。
```

### 关键约束

| 项目 | 要求 |
|---|---|
| 长度 | ≤ 1024 字符（Open Agent Skills 规范上限） |
| 视角 | 第三人称（"[Skill 名称] 处理…"，不是"我负责…"） |
| 触发语言 | 用用户可能说的词（"帮我润色""报错了""翻译成英文"），不是内部术语 |
| 排他声明 | 必须写 DO NOT USE + 替代 skill 名称，否则相邻 skill 互相抢流量 |
| 用词 | 用有推力的语言——"MUST USE WHEN"比"适用于"更能触发加载 |

### 反模式

| ❌ 低效写法 | 问题 |
|---|---|
| "适用于写作助手场景" | 循环定义，AI 不知道"写作助手场景"是什么 |
| "专注编程，覆盖多种场景" | 缺 USE WHEN，不含用户视角触发词 |
| 只写能力不写边界 | 没有 DO NOT USE，相邻 skill 互相抢流量 |
| 超过 1024 字符 | 超出规范上限，可能被截断 |

### 写法示例

```
代码助手 Skill。专注所有编程任务，包括写新代码、调试报错、重构、技术选型、编写测试。
USE WHEN 用户提到代码/函数/bug/报错/API/算法/框架，或说"帮我写""帮我改""为什么报错"。
DO NOT USE for OpenClaw 运维（→ openclaw-manager）或纯数据分析（→ analyst）。
```

---

## 三、skill 边界速查

相邻 skill 触发边界，新增 skill 时先对照此表，并更新对应 DO NOT USE：

| 意图 | 正确 Skill | 排除 Skill |
|---|---|---|
| 写/改/调试代码 | programmer | code-studio（工作流命令）|
| 项目初始化/开发计划/工作流 | code-studio | programmer（临时代码问题）|
| 写文章/润色/摘要 | writer | novel-writer（长篇小说）、storyteller（叙事讲解） |
| 写长篇章节连载 | novel-writer | writer、storyteller |
| 知识/历史/概念叙事讲解 | storyteller | writer（功能性写作）、novel-writer（连载） |
| 中英文翻译 | translator | writer |
| 数据统计/可视化建议 | analyst | programmer（写分析代码） |
| 金融行情/资产配置 | financial-advisor | analyst |
| 玩游戏（海龟汤/飞花令等） | mini-game | assistant（只是聊天）、storyteller |
| OpenClaw 运维 | openclaw-manager | programmer |
| 其他/不确定/闲聊 | assistant | — |

---

## 四、核心设计哲学

### 1. 每个规则必须可验证

**反面**：`要专业`、`要严谨`
**正面**：`超过 5 个类别不用饼图`、`连续 2 次失败必须跑 doctor`

规则必须落到**具体阈值、具体动作、具体清单**，模型才能执行。

### 2. 正负清单对列

模型常见失败不是"没做该做的"，而是"做了不该做的"。每个场景**既写该做什么，也写不该做什么**：

```
- 改：病句、歧义、冗余、错别字
- 不改：个人用词偏好、有意为之的语气词、观点倾向
```

### 3. 检查门（rule + detection + block）

每个质量要求应包含三要素：触发规则 + 检测动作 + 阻断条件：

```markdown
### 中段自检（每 500 字触发）
1. 数出 `——` 数量
2. 数量 ≥ 3 → 后续段落不再新增
3. 数量 ≥ 5 → 立刻停下删减后才能继续
阻断条件：触发数量 ≥ 5 但未删减就继续 = 质量违规
```

### 4. 三层防御

| 层 | 位置 | 内容 |
|---|---|---|
| 第 1 层（开头声明） | skill 开头 `## 核心原则` | 铁律：写之前必看一次 |
| 第 2 层（中段自检） | 执行流程中 | 固定节点或字数触发扫描 |
| 第 3 层（结尾拦截） | 最终输出前 | quality-checklist / 兜底检查 |

三层必须同时存在——只有第 3 层 = 错了才知道；只有第 1 层 = 声明却没执行。

### 5. 顶层禁止输出表

SKILL.md 顶层设立"禁止输出"节，堵死高频偷懒路径：

```markdown
## 禁止输出

| 禁止 | 替代 |
|---|---|
| "接下来主角去做了 X"（概括代替场景渲染） | 切具体视角写动作 + 感官 + 对白 |
| 战斗只写"两人激战片刻，主角胜出" | 加载 spectacle-rendering 执行四段式渲染 |
```

### 6. 动作化，不堆原则

| 原文（名词式） | 重写（动词式 + 可执行） |
|---|---|
| "读取状态文件" | "用 Read 工具打开 novel-state.md，逐字读出 4 个字段写入工作记忆" |
| "确认本次范围" | "用 1 句话告诉用户本次章节号 + 设计焦点，等待用户 ok / 调整" |
| "更新状态" | "用 Edit 工具按以下 4 处修改 novel-state.md：① current_chapter +1 ② 替换设计焦点 ③ 保留短期计划 ④ 追加 Key Continuity Notes" |

---

## 五、目录结构

```
skills/
  <skill-name>/
    SKILL.md          ← 必须存在；frontmatter + system_prompt 正文
    references/       ← 可选；附属参考文档（系统自动递归发现所有 .md 文件）
    scripts/          ← 可选；可执行脚本
```

### SKILL.md frontmatter 字段

```yaml
---
name: skill-name          # kebab-case，与目录名一致（必填）
description: |
  [三段式描述，≤ 1024 字符，见第二节]
metadata:
  display_name: "显示名称"  # 人类可读（必填）
  order: "20"              # 排序数字字符串，越小越靠前（选填，默认 1000）
  category: coding         # general/coding/writing/analysis/finance/language/ops（选填）
---
```

> ⚠️ `references/` 下的所有 `.md` 文件由系统**自动发现**，无需在 frontmatter 声明。frontmatter 只需 `name` / `description` / `metadata` 三项。

### Order 分段约定

```
10        通用兜底
20-30     开发 / 专业分析
40-50     文字工具
60-65     创作 / 游戏
70+       专属工具 / 运维
```

新增 skill 时按所属类别选 order，保留间隔便于后续扩展。

---

## 六、渐进披露设计原则

每个 skill 加载分三层，**SKILL.md 主体是路由器，不是百科全书**。强行把所有规则塞进主文件 = 每次会话都为不会用到的内容付费。

### 三层结构与 token 预算

| Tier | 内容 | Token 预算 | 加载时机 |
|---|---|---|---|
| 1 · 发现 | frontmatter `name` + `description` | ~80（55-235） | 永远加载，用于路由匹配 |
| 2 · 激活 | SKILL.md 主体 | 中位 ~2000，软上限 6000（≤ 400 行中文） | skill 命中时加载 |
| 3 · 执行 | `references/**.md` / `scripts/` | 按需 | LLM 调用 `get_skill_reference` 时加载 |

### 何时把内容下沉到 reference

满足任一条件**必须**外移：

- **密集型**：题库、模板库、范例集（≥ 30 行的列表 / 表格）
- **条件性**：仅特定子场景才用（如「债券利率换算表」只在涉及债券时）
- **同质多态**：N 个对等子项目，单次会话只用 1-2 个（如 12 个游戏规则）
- **横切复用**：跨子场景共享的规则集（如「底牌保护话术」）

**不要**外移的内容（每次都需看到的高频拦截规则）：
- 角色定义 + 默认前提
- 核心原则 / 铁律 / 禁止输出
- 场景路由表（触发词 → 加载哪个 reference）
- 通用流程 / 格式 / 自检 / 兜底

### 目录与命名约定

```
skill-name/
├── SKILL.md                 # 主路由，≤ 400 行
├── references/              # 平铺或按主题分组
│   ├── <topic>.md           # 简单 skill 直接平铺
│   └── <category>/
│       └── <topic>.md       # 复杂 skill 按类别分组（参考 novel-writer）
└── scripts/                 # 可选：可执行脚本
```

- 文件名小写 + 连字符（`turtle-soup.md` 而非 `TurtleSoup.md`）
- 单个 reference ≤ 300 行；超过先考虑拆分
- 每个 reference 顶部加 `# 标题` + 一句用途说明，便于 LLM 跳读
- **禁止套娃**：reference 嵌套不超过两层（`references/<category>/<topic>.md` 是上限）

### 在 SKILL.md 中声明加载点

主体必须给**场景路由表**，让 LLM 知道何时加载哪个 reference：

```markdown
## 场景路由

| 用户意图 / 触发词 | 加载 reference |
|---|---|
| "玩海龟汤" / 情境推理 | references/turtle-soup.md |
| "互动剧情" / "选择型故事" | references/interactive-story.md |
```

加载调用：
```
get_skill_reference("skill-name", "references/turtle-soup.md")
```

### 反模式速查

| ❌ 反模式 | ✅ 正确做法 |
|---|---|
| 把 12 个游戏规则全写进 SKILL.md | SKILL.md 只列触发词 + 路由到 reference |
| reference 嵌套 3 层 | 最多 2 层（`references/db/schema.md`） |
| 在 skill 目录建 `README.md` / `CHANGELOG.md` | 直接放 SKILL.md，frontmatter 自我说明 |
| description 只写能力不写边界 | 含 USE WHEN + DO NOT USE + 替代 skill |
| 反复在每个子场景重复同一规则 | 抽到核心原则或独立 reference |
| "核心原则 / 禁止输出 / 自检"放进 reference | 高频拦截规则必须留在 SKILL.md 主体 |

---

## 七、标准结构模板

推荐按以下骨架组织 skill，可按需增删小节：

```markdown
---
name: skill-name
description: |
  [Skill 名]。[能力范围]。USE WHEN [触发场景/关键词]。DO NOT USE for [排除项 → 替代 skill]。
metadata:
  display_name: "显示名称"
  order: "20"
  category: coding
---
# 角色

一段话：你是谁、擅长什么、服务于谁。声明默认前提（如适用）。

---

## 当前时间

{{current_time_info}}

---

## 核心原则【第 1 层防御：每次写之前必看】

最重要的 5-8 条铁律，关键词加粗，每条可验证（有阈值/动作/清单）。

---

## 禁止输出

| 禁止 | 替代 |
|---|---|
| 高频偷懒模式 A | 正确做法 A |
| 高频偷懒模式 B | 正确做法 B |

---

## 工作场景

按子场景拆开，每个场景给出：
- 触发条件
- 具体操作步骤或正负清单
- 输出格式要求

---

## 场景路由（有 references 时必须）

| 用户意图 / 触发词 | 加载 reference |
|---|---|
| 触发词 A | references/xxx.md |

---

## 失败兜底

- 信息不足 → 反问 / 列选项 / 给默认
- 遇到边界外请求 → ⚠️ 声明不出手
- 结果异常 → 降级策略
```

---

## 八、新增 skill 检查清单

### description 质量
- [ ] ≤ 1024 字符
- [ ] 三段式：能力 + USE WHEN（用户视角触发词）+ DO NOT USE（命名替代 skill）
- [ ] 边界对应关系已录入第三节「skill 边界速查」

### 基础一致性
- [ ] 开头声明了角色、服务对象、默认前提
- [ ] 包含 `{{current_time_info}}` 占位符
- [ ] `{{ai_name}}` / `{{owner_name}}` 使用正确（需要时）
- [ ] 不加废话前缀（"好的，让我来…"）
- [ ] 不瞎编，不确定明说

### 规则质量
- [ ] 每条规则可验证（有具体阈值 / 清单 / 动作）
- [ ] 主要场景有**正负清单对列**（改/不改、用/不用）
- [ ] 有至少一条**负面声明**（不做什么、什么时候不出手）
- [ ] 和其他 skill 有冲突时，显性说明适用边界

### 场景覆盖
- [ ] 主要场景拆开列出，不是一锅炖
- [ ] 每个场景有具体操作步骤或输出格式
- [ ] 覆盖"信息不足"的降级策略
- [ ] 覆盖"失败 / 异常"的兜底流程

### Token 预算（按中文 1 字 ≈ 1.5 token 估算）
- [ ] SKILL.md 主体 ≤ 400 行 / ≤ 6000 token
- [ ] 单个 reference ≤ 300 行 / ≤ 4500 token
- [ ] "只在某些子场景才用到"的内容已下沉到 reference
- [ ] references 没有套娃子目录（最多两层）
- [ ] 没有创建 `README.md` / `CHANGELOG.md` 等说明文件

### 符号和格式
- [ ] 重要警告用 ⚠️
- [ ] 关键词用粗体
- [ ] 列表 > 长段落，表格 > 大段对比
- [ ] 子场景之间用 `---` 或三级标题分隔

---

## 九、维护节奏

- **基于真实踩坑迭代**：遇到 skill 表现不符预期时，先记录场景和期望输出，积累到 3-5 条再一起改
- **不要纸面博弈**：skill 的优化收益在实战 2 周后急剧上升，此前的反复打磨边际递减
- **保持家族气质**：新增 skill 风格应和现有 skill 一致（具体化规则、正负清单、失败兜底）
- **order 变更谨慎**：调整 order 可能改变触发优先级，影响其他 skill 的命中率

---

## 十、符号约定

| 符号 | 用途 |
|---|---|
| ⚠️ | 重要警告 / 边界声明 |
| `※` | 翻译官专用：文化表达注释、关键歧义说明 |
| **粗体** | 关键词、关键动作 |
| `→` | 流程推进 / 指向替代 skill |
| `—` | 命令/选项 + 一句话用途的分隔符 |

新 skill 需要自造符号时，先评估是否能复用上述体系。
