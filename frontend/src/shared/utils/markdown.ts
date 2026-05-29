import { createHighlighter, type BundledLanguage, type BundledTheme, type Highlighter } from 'shiki';

/** 预加载的语言列表，覆盖开发场景常用语言 */
const SHIKI_LANGS: BundledLanguage[] = [
  'typescript', 'javascript', 'tsx', 'jsx',
  'python',
  'bash', 'sh', 'shell',
  'json', 'yaml', 'toml',
  'html', 'css', 'scss',
  'sql',
  'rust', 'go', 'java', 'c', 'cpp',
  'markdown',
];

/** 预加载的主题 */
const SHIKI_THEMES: BundledTheme[] = ['github-light', 'github-dark'];

/** 支持的语言 Set，用于安全判断 */
export const SUPPORTED_SHIKI_LANGS = new Set<string>(SHIKI_LANGS);

/** 单例 Promise，全局只创建一次 highlighter */
let highlighterPromise: Promise<Highlighter> | null = null;

/**
 * 获取 Shiki highlighter 单例（懒加载，首次调用时初始化）。
 * 后续调用直接复用同一实例，无性能开销。
 */
export function getShikiHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: SHIKI_THEMES,
      langs: SHIKI_LANGS,
    });
  }
  return highlighterPromise;
}

/**
 * 修复流式传输过程中不完整的 Markdown 语法，防止 react-markdown 产生畸形 HTML。
 *
 * 目前处理的情况：
 *   - 未闭合的代码围栏（code fence）：追加 ``` 关闭它
 *
 * 其余不完整语法（**、_、表格等）react-markdown 会 graceful 降级为纯文本，无需手动修复。
 */
export function patchStreamingMarkdown(text: string): string {
  const fenceCount = (text.match(/```/g) ?? []).length;
  return fenceCount % 2 !== 0 ? `${text}\n\`\`\`` : text;
}
