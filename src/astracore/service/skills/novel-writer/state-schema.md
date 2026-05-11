# State File Schema

Four files maintain the novel's persistent state. All live in the project directory.

---

## novel-state.md

The only file updated after every writing session.

```markdown
# Novel State

## Project
- **title**: 小说标题
- **genre**: web-novel | literary | genre-fiction
- **subgenre**: 具体子类型（如：玄幻/升级流，literary/意识流，genre/推理）
- **project_dir**: /absolute/path/to/project
- **target_length**: 目标总字数（如：500000）
- **created**: YYYY-MM-DD
- **last_updated**: YYYY-MM-DD

## Progress
- **chapters_completed**: 0
- **total_chapters_planned**: 100
- **current_chapter**: 1
- **word_count_total**: 0

## Completed Chapters
<!-- 每章一行：章节号、标题、字数、一句话摘要 -->
<!-- 示例：第1章《初入江湖》3200字 — 主角李云离开故乡，遭遇第一次危机 -->

## Current Design Focus
<!-- 当前这段剧情的核心设计意图：要传达什么、推进什么、埋什么伏笔 -->

## Short-term Plan
<!-- 接下来3-5章的安排：每章一行，说明主要事件和目的 -->
<!-- 示例：
- 第2章：李云抵达碧剑宗，建立与二师兄的关系（为后续反转埋线）
- 第3章：第一次修炼突破，展示金手指核心机制，给读者第一个爽点
-->

## Key Continuity Notes
<!-- 跨章节需要保持一致的细节：已透露的信息、伏笔状态、角色的具体行为/台词、道具/数字等 -->
<!-- 每条注明出处章节，已解决的伏笔可删除 -->
<!-- 涉及"谁知道什么"的信息，在条目末尾用方括号标注知情状态，防止信息流向写反 -->
<!-- 示例：
- 父亲左腿跛行（第1章），原因未明——伏笔，等待揭露时机
- 周牧入门花了80两银子（第2章）——已设定，后续提及需保持一致
- 封印第一层已碎（第5章）[A ch5已知; B ch8已知; 宗门长老 ch13起疑]
-->
```

---

## novel-framework.md

Updated when the user revises world, plot, or story structure. Rarely changes after initial design.

```markdown
# Framework

## World & Setting
<!-- 世界观、时代背景、地理、规则体系、权力结构、独特元素 -->

## Premise & Core Conflict
<!-- 故事的核心矛盾是什么？驱动整个故事前进的根本问题 -->

## Plot Structure
<!-- 选择结构模式并填写：
     网文：起点爽点 → 成长线 → 中期高潮 → 危机 → 最终战
     文学：三幕式 / 英雄之旅 / 自定义
     类型：类型专属结构（见 genre-conventions.md）
-->

## Key Turning Points
<!-- 列出5-10个必须发生的剧情节点，每个节点说明：发生了什么、为什么重要 -->

## Ending Direction
<!-- 结局的大方向：开放/封闭，悲/喜/悲喜交织，主角的最终状态 -->

## Genre Conventions Applied
<!-- 从 genre-conventions.md 选用的套路，说明如何落地 -->
```

---

## novel-characters.md

Updated when new important characters appear or characters undergo major development.

```markdown
# Characters

## [主角名]
- **角色定位**: 主角
- **年龄/背景**: 
- **外貌特征**: 
- **核心性格**: （3-5个关键词 + 1-2句说明）
- **内心缺陷/创伤**: （驱动人物弧的根本原因）
- **核心驱动**: （他/她最想要什么？）
- **成长弧线**: 起点状态 → 转折点 → 终点状态
- **与其他角色的关系**: 

---

## [配角名]
<!-- 同上结构，简化版 -->

---

## 关系图
<!-- 文字描述角色间关键关系，复杂时可用列表 -->
```

---

## novel-style.md

Defined once during framework design or style analysis. Rarely updated.

```markdown
# Style Guide

## Source
原创设计 | 模仿自：《书名》（作者）

## Narrative Perspective
<!-- POV类型 + 叙述距离 -->
<!-- 示例：第三人称有限视角，紧贴主角内心，偶尔拉远展示全局 -->

## Tense
过去时 | 现在时

## Voice & Tone
<!-- 叙述语气、情感基调、幽默方式（如有） -->

## Prose Style
<!-- 句子长度倾向、节奏特征、词汇层级、意象密度、心理描写频率 -->

## Pacing
<!-- 章节目标字数、场景与概述比例、高潮节奏、章末处理方式 -->
<!-- 网文额外：爽点密度目标（如每1500字一个小爽点，每章末一个大爽点） -->

## Dialogue Style
<!-- 对话占比、归属标注方式、角色语言差异化方法、潜台词密度 -->

## Signature Elements
<!-- 标志性写法、反复出现的意象或结构、开篇钩子风格、章末风格 -->
```
