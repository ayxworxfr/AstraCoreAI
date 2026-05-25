/**
 * 禁用词检测与统计脚本
 * 
 * 功能：扫描章节文件中的一级/二级禁用词，输出统计和位置
 * 注意：禁用词的替换需要语义判断，本脚本只做检测和定位，
 *       实际替换建议由AI逐条审阅处理。
 * 
 * 用法：
 *   node fix-banned-words.js <目录或文件路径> [--fix-simple]
 * 
 * --fix-simple: 对可安全删除的词（微微、轻轻、缓缓、淡淡）直接删除
 *              （因为动词本身已含"轻/缓"义，删除后句子通顺）
 * 
 * 示例：
 *   node fix-banned-words.js "D:/project/StoryVault/星际破防指南/vol-02"
 *   node fix-banned-words.js "D:/project/StoryVault/星际破防指南/vol-02" --fix-simple
 */

const fs = require('fs');
const path = require('path');

// ============ 禁用词表 ============

// 一级禁用词：直接删除或有固定替换
const LEVEL1_WORDS = {
  '微微': { action: 'delete', note: '删除（动词已含义）' },
  '轻轻': { action: 'delete', note: '删除（动词已含义）' },
  '缓缓': { action: 'replace', replacement: '慢慢', note: '→慢慢（或删除）' },
  '淡淡': { action: 'delete', note: '删除' },
  '不禁': { action: 'delete', note: '删除' },
};

// 二级禁用词：需要上下文判断，仅标记
const LEVEL2_WORDS = [
  '一丝', '隐约', '竟然', '居然', '忍不住',
  '仿佛', '似乎', '好像', '宛如', '犹如',
  '心中一动', '心头一震', '心中暗道',
  '嘴角微扬', '嘴角上扬', '嘴角勾起',
  '深吸一口气', '长舒一口气',
  '眼中闪过', '目光一凝', '瞳孔一缩',
];

// ============ 命令行解析 ============
const args = process.argv.slice(2);
let targetPath = '';
let fixSimple = false;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--fix-simple') {
    fixSimple = true;
  } else if (!targetPath) {
    targetPath = args[i];
  }
}

if (!targetPath) {
  console.log('用法: node fix-banned-words.js <目录或文件路径> [--fix-simple]');
  process.exit(1);
}

// ============ 核心逻辑 ============

function processFile(filePath) {
  let content = fs.readFileSync(filePath, 'utf-8');
  const fileName = path.basename(filePath);
  const findings = { level1: {}, level2: {}, total: 0 };
  let modified = false;

  // 检测一级禁用词
  for (const [word, config] of Object.entries(LEVEL1_WORDS)) {
    const regex = new RegExp(word, 'g');
    const matches = content.match(regex);
    if (matches && matches.length > 0) {
      findings.level1[word] = {
        count: matches.length,
        action: config.note
      };
      findings.total += matches.length;

      // --fix-simple 模式：执行安全替换
      if (fixSimple) {
        if (config.action === 'delete') {
          content = content.replace(regex, '');
          modified = true;
        } else if (config.action === 'replace' && config.replacement) {
          content = content.replace(regex, config.replacement);
          modified = true;
        }
      }
    }
  }

  // 检测二级禁用词
  for (const word of LEVEL2_WORDS) {
    const regex = new RegExp(word, 'g');
    const matches = content.match(regex);
    if (matches && matches.length > 0) {
      findings.level2[word] = matches.length;
      findings.total += matches.length;
    }
  }

  if (modified) {
    fs.writeFileSync(filePath, content, 'utf-8');
  }

  return { file: fileName, findings, modified };
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
  } else {
    files = [targetPath];
  }

  if (files.length === 0) {
    console.log('未找到 chapter-*.md 文件');
    process.exit(1);
  }

  console.log('');
  console.log('='.repeat(60));
  console.log(`  禁用词扫描${fixSimple ? ' [自动修复简单项]' : ' [仅检测]'}`);
  console.log(`  目标: ${targetPath}`);
  console.log(`  文件数: ${files.length}`);
  console.log('='.repeat(60));
  console.log('');

  const globalStats = { level1: {}, level2: {} };
  let totalIssues = 0;
  let filesWithIssues = 0;

  for (const file of files) {
    const result = processFile(file);
    
    if (result.findings.total > 0) {
      filesWithIssues++;
      const fixedMark = result.modified ? ' [已修复]' : '';
      console.log(`  ${result.file}: ${result.findings.total}处${fixedMark}`);
      
      // 一级
      for (const [word, info] of Object.entries(result.findings.level1)) {
        console.log(`    [L1] "${word}" x${info.count} ${info.action}`);
        globalStats.level1[word] = (globalStats.level1[word] || 0) + info.count;
      }
      // 二级
      for (const [word, count] of Object.entries(result.findings.level2)) {
        console.log(`    [L2] "${word}" x${count}`);
        globalStats.level2[word] = (globalStats.level2[word] || 0) + count;
      }
      
      totalIssues += result.findings.total;
    }
  }

  // 总结
  console.log('');
  console.log('-'.repeat(60));
  console.log(`  总计: ${totalIssues} 处问题，涉及 ${filesWithIssues}/${files.length} 个文件`);
  
  if (Object.keys(globalStats.level1).length > 0) {
    console.log('');
    console.log('  一级禁用词汇总:');
    for (const [word, count] of Object.entries(globalStats.level1)) {
      console.log(`    "${word}": ${count}处`);
    }
  }
  
  if (Object.keys(globalStats.level2).length > 0) {
    console.log('');
    console.log('  二级禁用词汇总（需人工审阅）:');
    for (const [word, count] of Object.entries(globalStats.level2)) {
      console.log(`    "${word}": ${count}处`);
    }
  }

  if (!fixSimple && totalIssues > 0) {
    console.log('');
    console.log('  提示: 添加 --fix-simple 可自动修复一级禁用词');
  }
  console.log('');
}

main();
