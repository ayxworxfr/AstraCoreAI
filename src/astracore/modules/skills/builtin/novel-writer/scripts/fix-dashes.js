/**
 * 破折号智能修复脚本
 * 
 * 功能：将每章破折号数量控制在 ≤5 处，优先保留合法用途
 * 
 * 用法：
 *   node fix-dashes.js <目录或文件路径> [--dry-run] [--max <数量>]
 * 
 * 示例：
 *   node fix-dashes.js "D:/project/StoryVault/星际破防指南/vol-02" --dry-run
 *   node fix-dashes.js "D:/project/StoryVault/星际破防指南/vol-02" --max 5
 *   node fix-dashes.js "D:/project/StoryVault/星际破防指南/vol-02/chapter-013.md"
 * 
 * 规则优先级（保留分数越高越优先保留）：
 *   1. 对话被打断（引号内，后接引号结束）        → 保留分 100
 *   2. 成对强调性插入（前后各一个破折号）         → 保留分 80
 *   3. 悬停/未完成句（句末或段末）               → 保留分 70
 *   4. 解释说明（前后都有内容，非对话）           → 保留分 30
 *   5. 语气转折（可用逗号替代）                  → 保留分 10
 * 
 * 替换策略：
 *   - 对话中非打断的破折号 → 逗号
 *   - 叙述中的解释说明 → 逗号或删除
 *   - 语气转折 → 逗号
 *   - 列举/并列 → 逗号
 *   - 前后有空格的装饰性破折号 → 删除
 */

const fs = require('fs');
const path = require('path');

// ============ 配置 ============
const DASH = '\u2014\u2014'; // ——
const MAX_PER_CHAPTER = 5;

// ============ 命令行解析 ============
const args = process.argv.slice(2);
let targetPath = '';
let dryRun = false;
let maxDashes = MAX_PER_CHAPTER;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--dry-run') {
    dryRun = true;
  } else if (args[i] === '--max') {
    maxDashes = parseInt(args[i + 1]) || MAX_PER_CHAPTER;
    i++;
  } else if (!targetPath) {
    targetPath = args[i];
  }
}

if (!targetPath) {
  console.log('用法: node fix-dashes.js <目录或文件路径> [--dry-run] [--max <数量>]');
  process.exit(1);
}

// ============ 核心逻辑 ============

/**
 * 分析单个破折号的上下文，返回保留分数和替换建议
 */
function analyzeDash(content, index) {
  const before = content.substring(Math.max(0, index - 50), index);
  const after = content.substring(index + 2, Math.min(content.length, index + 52));
  
  const result = {
    position: index,
    before: before.slice(-15),
    after: after.slice(0, 15),
    score: 0,
    replacement: '\uFF0C', // 默认替换为逗号
    reason: ''
  };

  // ---- 规则1：对话被打断 ----
  // 模式A："……——"  破折号后紧跟关闭引号
  if (/^["\u201D]/.test(after)) {
    result.score = 100;
    result.replacement = DASH;
    result.reason = '对话被打断(后接引号)';
    return result;
  }
  
  // 模式B：破折号后1-3字符内有关闭引号（"你别——算了"）
  const closeQuoteNear = after.substring(0, 6).search(/["\u201D]/);
  if (closeQuoteNear >= 0 && closeQuoteNear <= 4) {
    // 检查是否在对话内
    const lastOpen = before.lastIndexOf('\u201C');
    const lastClose = before.lastIndexOf('\u201D');
    if (lastOpen > lastClose || lastOpen >= 0 && lastClose < 0) {
      result.score = 95;
      result.replacement = DASH;
      result.reason = '对话被打断(近引号)';
      return result;
    }
  }

  // ---- 规则2：成对强调性插入 ----
  // 检查前方60字符内是否有另一个破折号（说明当前是后半对）
  const beforeLong = content.substring(Math.max(0, index - 60), index);
  const prevDashPos = beforeLong.lastIndexOf(DASH);
  if (prevDashPos >= 0) {
    // 确认中间没有句号/换行（说明是同一句内的成对）
    const between = beforeLong.substring(prevDashPos + 2);
    if (!/[\u3002\n]/.test(between)) {
      result.score = 80;
      result.replacement = DASH;
      result.reason = '成对强调(后半)';
      return result;
    }
  }
  
  // 检查后方60字符内是否有配对破折号（说明当前是前半对）
  const afterLong = content.substring(index + 2, Math.min(content.length, index + 62));
  const nextDashPos = afterLong.indexOf(DASH);
  if (nextDashPos >= 0 && nextDashPos <= 40) {
    const between = afterLong.substring(0, nextDashPos);
    if (!/[\u3002\n]/.test(between)) {
      result.score = 80;
      result.replacement = DASH;
      result.reason = '成对强调(前半)';
      return result;
    }
  }

  // ---- 规则3：悬停/未完成句 ----
  // 破折号后是换行、段末、或极少文字后换行
  const afterTrimmed = after.replace(/^\s+/, '');
  if (afterTrimmed.length === 0 || /^\n/.test(afterTrimmed) || /^.{0,2}\n/.test(afterTrimmed)) {
    result.score = 70;
    result.replacement = DASH;
    result.reason = '悬停/未完成句';
    return result;
  }

  // ---- 规则4：判断是否在对话中 ----
  const lastOpenQuote = before.lastIndexOf('\u201C');
  const lastCloseQuote = before.lastIndexOf('\u201D');
  const inDialogue = (lastOpenQuote >= 0) && (lastCloseQuote < lastOpenQuote);

  if (inDialogue) {
    // 对话中的非打断破折号 → 逗号
    result.score = 20;
    result.replacement = '\uFF0C';
    result.reason = '对话内语气→逗号';
    return result;
  }

  // ---- 规则5：叙述中的解释说明 ----
  // 前后都是汉字
  if (/[\u4e00-\u9fff\uff01-\uff5e]$/.test(before) && /^[\u4e00-\u9fff\uff01-\uff5e]/.test(after)) {
    result.score = 30;
    result.replacement = '\uFF0C';
    result.reason = '叙述解释→逗号';
    return result;
  }

  // ---- 规则6：兜底 ----
  result.score = 10;
  result.replacement = '\uFF0C';
  result.reason = '其他→逗号';
  return result;
}

/**
 * 处理单个文件
 */
function processFile(filePath) {
  let content = fs.readFileSync(filePath, 'utf-8');
  
  // 找到所有破折号位置
  let positions = [];
  let searchFrom = 0;
  while (true) {
    const idx = content.indexOf(DASH, searchFrom);
    if (idx === -1) break;
    positions.push(idx);
    searchFrom = idx + 2;
  }

  const totalCount = positions.length;
  if (totalCount <= maxDashes) {
    return { file: path.basename(filePath), before: totalCount, after: totalCount, removed: 0, changes: [] };
  }

  // 分析每个破折号
  const analyses = positions.map(pos => analyzeDash(content, pos));
  
  // 按保留分数排序（高分优先保留）；同分时保留靠前的（更可能是重要位置）
  const sorted = [...analyses].sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.position - b.position;
  });
  
  // 保留前 maxDashes 个
  const toKeep = new Set(sorted.slice(0, maxDashes).map(a => a.position));
  
  // 从后往前替换（避免位置偏移）
  const changes = [];
  const toReplace = analyses
    .filter(a => !toKeep.has(a.position))
    .sort((a, b) => b.position - a.position); // 从后往前

  for (const item of toReplace) {
    changes.push({
      pos: item.position,
      context: `...${item.before}——${item.after}...`,
      replacement: item.replacement === DASH ? '(保留)' : item.replacement,
      reason: item.reason
    });
    if (item.replacement !== DASH) {
      content = content.substring(0, item.position) + item.replacement + content.substring(item.position + 2);
    }
  }

  // 验证最终数量
  let finalCount = 0;
  searchFrom = 0;
  while (true) {
    const idx = content.indexOf(DASH, searchFrom);
    if (idx === -1) break;
    finalCount++;
    searchFrom = idx + 2;
  }

  if (!dryRun) {
    fs.writeFileSync(filePath, content, 'utf-8');
  }

  return {
    file: path.basename(filePath),
    before: totalCount,
    after: finalCount,
    removed: totalCount - finalCount,
    changes: changes.reverse()
  };
}

// ============ 主流程 ============

function main() {
  let stat;
  try {
    stat = fs.statSync(targetPath);
  } catch (e) {
    console.error(`错误: 路径不存在 - ${targetPath}`);
    process.exit(1);
  }

  let files = [];

  if (stat.isDirectory()) {
    const entries = fs.readdirSync(targetPath)
      .filter(f => f.match(/^chapter-\d+\.md$/))
      .sort();
    files = entries.map(f => path.join(targetPath, f));
  } else if (stat.isFile()) {
    files = [targetPath];
  }

  if (files.length === 0) {
    console.log('未找到 chapter-*.md 文件');
    process.exit(1);
  }

  console.log('');
  console.log('='.repeat(60));
  console.log(`  破折号智能修复${dryRun ? ' [预览模式]' : ''}`);
  console.log(`  目标: ${targetPath}`);
  console.log(`  文件数: ${files.length}`);
  console.log(`  每章上限: ${maxDashes}`);
  console.log('='.repeat(60));
  console.log('');

  let totalBefore = 0;
  let totalAfter = 0;
  let totalRemoved = 0;
  const results = [];

  for (const file of files) {
    const result = processFile(file);
    results.push(result);
    totalBefore += result.before;
    totalAfter += result.after;
    totalRemoved += result.removed;

    const icon = result.before <= maxDashes ? '\u2713' : (result.after <= maxDashes ? '\u2713' : '\u2717');
    const changeInfo = result.removed > 0 ? ` (移除${result.removed}处)` : ' (无需修改)';
    console.log(`  ${icon} ${result.file}: ${result.before} -> ${result.after}${changeInfo}`);

    // dry-run 模式显示前几条变更详情
    if (dryRun && result.changes.length > 0) {
      const showMax = 3;
      for (let i = 0; i < Math.min(result.changes.length, showMax); i++) {
        const c = result.changes[i];
        console.log(`      [${c.reason}] ${c.context}`);
      }
      if (result.changes.length > showMax) {
        console.log(`      ... 还有 ${result.changes.length - showMax} 处变更`);
      }
    }
  }

  // 总结
  console.log('');
  console.log('-'.repeat(60));
  console.log(`  总计: ${totalBefore} -> ${totalAfter} (移除 ${totalRemoved} 处)`);
  console.log(`  通过: ${results.filter(r => r.after <= maxDashes).length}/${files.length} 章`);
  
  if (dryRun) {
    console.log('');
    console.log('  [!] 预览模式，未修改文件。去掉 --dry-run 执行实际修改。');
  } else if (totalRemoved > 0) {
    console.log('');
    console.log('  [OK] 修改已保存。');
  }
  console.log('');
}

main();
