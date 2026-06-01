# 扫榜执行指南

调用条件：用户在选题对话中要求查看市场榜单，或用户主动要求扫榜。
本指南只管"怎么执行"——字段格式、清洗规则见 `scan-output-format.md`。

---

## 执行决策树

```
1. 优先级 A：用脚本采集（首选，必须先尝试）
   ↓ 失败（Chrome 未启动 / CDP 端口冲突 / 反爬升级）
2. 优先级 B：WebSearch 检索热门书单文章作为兜底
   ↓ 仍无法获取
3. 优先级 C：调用 get_skill_reference("题材趋势") 用历史趋势作参考，向用户说明数据非实时
```

---

## 优先级 A：脚本采集

**第一步：启动 CDP（每次新会话执行一次）**

```bash
node "{{skill_dir}}/scripts/setup-cdp-chrome.js" 9222
```

**第二步：按需执行采集脚本**

| 脚本 | 用途 | 登录态要求 |
|---|---|---|
| `qidian-rank-scraper.js --type newsign` | 起点新人签约新书榜 | 无 |
| `qidian-rank-scraper.js --type all` | 起点全部榜单 | 无 |
| `qimao-rank-scraper.js --channel all --type all` | 七猫男频/女频 全榜 | 无 |
| `fanqie-rank-scraper.js` | 番茄全榜 | **需登录态** |
| `jjwxc-rank-scraper.js` | 晋江榜单 | 部分需登录态 |

```bash
SCRIPTS="{{skill_dir}}/scripts"
node "$SCRIPTS/qidian-rank-scraper.js" --type newsign
node "$SCRIPTS/qidian-rank-scraper.js" --type all
node "$SCRIPTS/qimao-rank-scraper.js" --channel all --type all
node "$SCRIPTS/fanqie-rank-scraper.js"
node "$SCRIPTS/jjwxc-rank-scraper.js"
```

**第三步：读取与解读**

1. 用 Read 工具读取 `扫榜/` 目录下的输出文件
2. 调用 `get_skill_reference("扫榜数据格式")` 对照各平台字段定义
3. 提炼 3-5 个热门题材趋势 + 数据支撑（"番茄男频近 7 日 X 题材有 N 本进入前 20"）

---

## 优先级 B：WebSearch 兜底

执行任一脚本失败 → 立刻 WebSearch 这些查询：
- 「起点中文网新书榜 [当前年月]」
- 「番茄小说热门题材 [当前年月]」
- 「晋江月榜 [当前年月]」

整理 3-5 个趋势，**明确告知用户**：本批数据来自第三方资讯，可能滞后 1-7 天。

---

## 禁止输出

| 禁止 | 替代 |
|---|---|
| 跳过脚本直接 WebSearch | 先尝试脚本，仅在 Bash 报错后才走 WebSearch |
| 仅说"现在 X 题材很火"无数据支撑 | 必须给出排名 / 在读数 / 收藏数等量化指标 |
| 未告知用户数据来源 | 始终说明：脚本实时 / WebSearch 兜底 / 历史趋势 |
