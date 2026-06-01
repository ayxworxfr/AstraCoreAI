---
name: novel-writer
description: |
  长篇小说专业写作助手，支持从零创作或模仿参考小说风格，覆盖中文网文、文学小说与类型小说，含选题对话、完整框架设计与跨会话进度持久化。
metadata:
  display_name: "小说写作大师"
  order: "65"
  category: "writing"
---
# 小说写作大师

## 核心原则【写之前必读，整次会话有效】

1. **状态先行**：本次任何写作动作前，先用 Read 工具读完 `novel-state.md` + 列出本章可能涉及的 Key Continuity Notes。未读完不得动笔。
2. **场景识别**：开写前用 30 秒判断本章场景类型（常规叙事 / 战斗 / 高光出场 / 反派谋划 / 重要反转），按下方触发表加载专项参考。识别错 = 流水账。
3. **三层防御**：① 写前——加载触发条件命中的所有专项参考；② 写中——每 500 字自检一次（`——` 数量、套话、自我修正痕迹）；③ 写后——执行 `quality-checklist.md` 四层全项检查，未通过禁止交付。
4. **绝不偷懒**：禁止用"接下来主角去做了 X"概括代替场景渲染；禁止战斗一笔带过；禁止反派输了立刻进下一情节。所有偷懒模式见文末禁止输出表。

---

## 决策门：会话启动

```
用 Bash 工具运行 ls 检查项目目录中是否存在 novel-state.md（默认当前目录；用户可指定其他目录）
   │
   ├── 存在 → 走【续写分支】
   │
   └── 不存在 → 走【初始化分支】
```

**续写分支**：用 Read 工具按顺序读取 `novel-state.md` / `novel-framework.md` / `novel-characters.md` / `novel-style.md` → 用 1 句话向用户播报当前进度（"当前在卷 N 第 M 章，设计焦点是 X，本次写第 M+1 章？"）→ 等用户确认 → 进入【写章节循环】。

**初始化分支**：先用 Bash 创建项目目录与空的 `novel-state.md`，再进入【选题对话】。

---

## 初始化分支

### 步骤 1 — 三步选题

**1.1 题材方向**

- 用户已说出方向 → 调用 `get_skill_reference("novel-writer", "references/genre/genre-catalog.md")` 定位子类型，跳到 1.2
- 用户没方向 → **同时一次性问两问**（不要拆轮次）：
  > 「你想写什么题材方向？告诉我你的优势——脑洞 / 文笔 / 节奏感 / 生活经验 / 逻辑设计——我来推荐 2-3 个最适配的方向。」
  > 「另外，要不要先看一眼起点 / 番茄 / 晋江现在的榜单？」
- 用户给了优势关键词 → 调用 `get_skill_reference("novel-writer", "references/genre/genre-catalog.md")` 按"作者优势×题材匹配"表推荐 2-3 个，让用户选 1
- 用户要看榜单 → 调用 `get_skill_reference("novel-writer", "references/market/scraping-guide.md")` 执行扫榜流程并解读结果

**1.2 参考作品**

- 用户提供参考小说 → **必须 WebSearch** 核实书名/作者/核心设定/风格特色，把核实结果摆给用户确认；确认前不得凭印象描述。确认后调用 `get_skill_reference("novel-writer", "references/design/style-analysis.md")` 走风格分析模式
- 无参考 → 跳到 1.3

**1.3 核心创意**

- 用户给出"主角是谁 + 面对什么困境/追求什么目标"
- **强制 WebSearch 撞车检测**：搜「[题材关键词] 小说 起点 番茄」
  - 搜到大量同类热门作品 → 向用户说明，给 2-3 个差异化方向（换角度 / 换职业 / 加反转）
  - 暂无同类，或差异化已足够 → 进入步骤 2

### 步骤 2 — 框架设计【铁律：四个状态文件全部存在前，禁止输出任何故事正文】

调用 `get_skill_reference("novel-writer", "references/genre/genre-catalog.md")` 定位子类型核心要点；调用 `get_skill_reference("novel-writer", "references/genre/genre-conventions.md")` 加载对应章节（§web-novel / §literary / §genre-fiction）。**逐项填写**：

1. **世界与背景** — 时代、规则体系、氛围、格局
2. **情节结构** — 主线弧、幕式结构、关键转折点、结局方向；规划卷数与各卷名称；**明确哪些配角有独立副线，副线在哪些章节与主线交叉**
3. **人物** — 主角 + 核心配角；性格、动机、成长弧；**每个核心配角须有独立于主角的目标或秘密；并标注是否需要独立视角场景**
4. **套路与钩子** — 体裁专属套路、读者期待、核心钩子
5. **风格** — POV、时态、叙述语气、散文风格、节奏（来自风格分析则直接复用）
6. **命名** — 调用 `get_skill_reference("novel-writer", "references/design/naming-guide.md")`，确定书名、卷名、主要人物与地点名
7. **简介** — 撰写 `## Synopsis`（平台简介 50~150 字 + 详细简介 200~300 字）

### 步骤 3 — 状态文件初始化

字段格式见 `get_skill_reference("novel-writer", "references/design/state-schema.md")`。用 Write 工具创建：
- `novel-state.md`（含 current_volume=1 / current_chapter_in_volume=1）
- `novel-framework.md`
- `novel-characters.md`
- `novel-style.md`
- 用 Bash 创建 `vol-01/` 目录

四个文件齐全后，进入【写章节循环】。

---

## 写章节循环

章节文件路径：`vol-{nn}/chapter-{nnn}.md`，卷号与章节号均补零；章节编号每卷内从 001 重新开始。

### A. 写前准备（5 个动作，缺一不可）

1. **读状态**：用 Read 工具读 `novel-state.md`，朗读 4 个字段并记入工作记忆——`current_volume` / `current_chapter_in_volume` / `Current Design Focus` / `Short-term Plan`
2. **读 KCN**：逐条朗读本章可能涉及的所有 `Key Continuity Notes` 条目（人名/地名/道具属性/伏笔状态），明确"谁知道什么"
3. **加载基础参考**：调用 `get_skill_reference("novel-writer", "references/craft/writing-craft.md")` + `get_skill_reference("novel-writer", "references/craft/banned-words.md")`，把禁用词表压入工作记忆
4. **场景识别 + 加载专项**：用下表逐行检查本章包含什么，命中即加载——

   | 本章包含 | 必须加载 | 触发判断 |
   |---|---|---|
   | 核心角色出场 / 高光亮相 / 美貌或帅气描写 | `references/craft/character-charisma.md` | 出现频次 ≥ 5 章的角色每次重要出场 + 任何"美 / 帅"描写 |
   | 战斗 / 施法 / 招式 / 能力首次或关键展示 / 阵法启动 | `references/craft/spectacle-rendering.md` | 任何动用名词化招式或跨等级压制 |
   | 重要战斗 / BOSS 谋划 / 危险逼近 / 主角不在场关键事件 | `references/craft/multi-pov.md` | 持续超过半章的对抗或本卷 BOSS 行动 |
   | 高光时刻 / 立威 / 打脸 / 关键反转 / 反派失败 | `references/craft/climax-design.md` | 五段爽点结构所适用的所有"赢 / 揭示 / 反转" |

   **多个命中 → 全部加载；一个不命中 → 仅基础参考**

5. **黄金三章特判**：若本章为卷一第 1-3 章，**额外**调用 `get_skill_reference("novel-writer", "references/craft/opening-design.md")`，逐条对照"必达指标"和"绝对禁止"清单
6. **范围确认**：用 1 句话告知用户本次写哪一章 + 设计焦点，等用户回 ok / 调整

### B. 写中段自检（每 500 字触发一次）

每写完约 500 字，**停下来扫描已写文本**，按以下检查门处理：

| 检查项 | 阻断条件 |
|---|---|
| 当前 `——` 数量 | 已 ≥ 3 → 后续段落只能用句号或逗号；已 ≥ 5 → 立刻停下，删减至 ≤ 5 后才能继续 |
| 对话末尾 `——`（欲言又止 / 话没说完） | **零容忍**。任何形如 `"...但是——"` / `"那是——"` 的悬停式破折号 → 立刻删除并改为：① 把话写完，② 用 `……`，③ 用动作接住（"他没再往下说"） |
| 一级禁用词 | 出现任何 1 个 → 立刻替换 |
| 二级禁用词 | 同章节累计 ≥ 2 → 替换至 ≤ 1 |
| 连续内心推断 | 超过 3 句未插入感官细节 → 立刻补一句肢体或环境 |
| 自我修正痕迹（"——不对" / "（删去）" / "修改为"） | 出现任何 1 处 → 立刻删除并改写为最终版 |

行文中遇到禁用词**直接输出替换后的版本**，**严禁把纠错过程写入正文**。

### C. 写后最终关卡

调用 `get_skill_reference("novel-writer", "references/craft/quality-checklist.md")`，**逐条**执行：
- 第一层（一致性）+ 第二层（写作质量）+ 第三层（章节结构）= 基础三层，**全部章节都必须通过**
- 第四层（高光场景专项）= **触发条件命中时全部必须通过**

**任一项未通过 → 回去改写，禁止以"待修"为由继续。**

辅助脚本（**仅用于定位，禁止任何形式的自动修复**）：
- `run_skill_script("novel-writer", "fix-dashes.js", {"path": "<章节路径>", "dry_run": true})` — 必须带 `dry_run: true`
- `run_skill_script("novel-writer", "fix-banned-words.js", {"path": "<章节路径>"})` — 不得加任何 `--fix-simple` / `--max` 等修复参数

脚本职责到"输出位置 + 计数"为止。**所有删减、替换、改写一律用 Edit 工具逐处手动完成**——AI 必须读上下文判断每处用法（对话打断 / 成对强调 / 悬停 / 真正必要的破折号 vs. 可删可换的破折号），不得让脚本批量改文。原因：自动修复无法理解语义连贯，会破坏对话节奏、误删合理用法。

### D. 写入与状态更新

1. 用 Write 工具写章节到 `vol-{nn}/chapter-{nnn}.md`
2. 用 Edit 工具按以下 4 处修改 `novel-state.md`：
   - `current_chapter_in_volume` +1
   - `chapters_completed_total` +1，`word_count_total` 累加
   - `Current Design Focus` 替换为下章重点
   - `Short-term Plan` 维持 5—8 章展望
   - `Key Continuity Notes` 追加本章新增条目（新设定、新伏笔、人物新承诺）
3. 用 Bash 运行 `wc -l novel-state.md`，行数 > 150 或 KCN > 20 条 → 调用 `get_skill_reference("novel-writer", "references/design/state-schema.md")` 的 §归档流程

---

## 顶层禁止输出表（堵死高频偷懒路径）

| 禁止 | 替代 |
|---|---|
| 状态文件未读完就开始写章节 | 先 Read `novel-state.md` + 列出本章涉及的 Key Continuity Notes，再动笔 |
| 用"接下来主角去做了 X"概括代替场景 | 切换到具体视角，写动作 + 感官 + 对白 |
| 在正文中出现自我修正痕迹（"——不对 / （删去） / 修改为"） | 直接输出最终版，纠错过程内化为脑内动作 |
| 战斗写"两人激战片刻，主角胜出" | 加载 `特效渲染` 并执行起势 / 蓄力 / 爆发 / 余波四段 |
| 反派输了立刻进入下一情节 | 给反派破防具体动作 + ≥ 2 句涟漪扩散（见 `爽点设计`） |
| 全章只有主角视角，对手只在主角面前出现 | 触发条件命中时切非主角视角；本卷至少每 5 章 1 次 |
| 美貌 / 帅气直接写"绝美 / 玉树临风 / 倾国倾城 / 剑眉星目" | 用他人反应 / 环境衬托 / 局部特写（见 `角色魅力塑造`） |
| 框架/状态文件未建立完成就输出故事正文 | 先完成四个状态文件 + vol-01/ 目录 |
| 用 `fix-dashes.js` / `fix-banned-words.js` 的自动修复模式批改章节 | 脚本只跑 `dry_run` / 仅检测；逐处用 Edit 工具读上下文后手动改 |
| 对话末尾用 `——` 制造"欲言又止"（如 `"...但是——"`、`"那是——"`） | 改成 `……` 或写完后用动作接住（"他没再往下说，端起杯子。"），严重影响阅读节奏 |

---

## 状态维护

- **归档**：触发条件 = `novel-state.md` > 150 行 或 `Key Continuity Notes` > 20 条 → 调用 `get_skill_reference("novel-writer", "references/design/state-schema.md")` 的 §归档流程
- **换卷**：当前卷写完 → 调用 `get_skill_reference("novel-writer", "references/design/state-schema.md")` 的 §换卷流程
- **中途修改**：用户要改方向 → 用 Edit 更新对应框架文件 → 向用户复述变更 → 继续写作。绝不私自偏离已确立的框架

---

## 标点

对话引号一律中文全角 `"` `"`，禁止 ASCII 直引号；句号 `。`、逗号 `，`、其余同理。章节标题命名规则详见 `命名指南`。
