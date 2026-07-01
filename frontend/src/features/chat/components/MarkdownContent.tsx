import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
} from 'react';
import { Button, Tooltip } from 'antd';
import { CheckOutlined, CopyOutlined } from '@ant-design/icons';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { useTypewriter } from '@/shared/hooks/useTypewriter';
import { copyText } from '@/shared/utils/clipboard';
import { getShikiHighlighter, patchStreamingMarkdown, SUPPORTED_SHIKI_LANGS } from '@/shared/utils/markdown';

/** 向下传递"当前气泡是否正在流式输出"，用于控制代码块的 shiki 高亮时机 */
const StreamingContext = createContext(false);

// ─── 代码块复制按钮 ─────────────────────────────────────────────────────────────

function CopyCodeButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    void copyText(code).then((ok) => {
      if (!ok) return;
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <Tooltip title={copied ? '已复制' : '复制代码'}>
      <Button
        className="md-code-copy-button"
        type="text"
        size="small"
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        onClick={handleCopy}
        aria-label={copied ? '已复制代码' : '复制代码'}
      >
        {copied ? '已复制' : '复制'}
      </Button>
    </Tooltip>
  );
}

// ─── 块级代码 ─────────────────────────────────────────────────────────────────

function CodeBlock({ children, className }: ComponentPropsWithoutRef<'code'>) {
  const rawLang = /language-(\w+)/.exec(className ?? '')?.[1] ?? '';
  // 只对已预加载的语言做高亮，其余 fallback 为纯文本
  const lang = SUPPORTED_SHIKI_LANGS.has(rawLang) ? rawLang : 'text';
  const code = String(children).replace(/\n$/, '');
  const isStreaming = useContext(StreamingContext);
  const appTheme = useSettingsStore((s) => s.theme);
  const [html, setHtml] = useState<string | null>(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    // 流式输出期间跳过高亮（代码块内容未稳定，逐帧高亮代价太高）
    if (isStreaming) {
      setHtml(null);
      return;
    }

    cancelRef.current = false;

    void getShikiHighlighter()
      .then((hl) => {
        if (cancelRef.current) return;
        const result = hl.codeToHtml(code, {
          lang,
          theme: appTheme === 'dark' ? 'github-dark' : 'github-light',
        });
        setHtml(result);
      })
      .catch(() => {
        // 高亮失败时静默降级为纯文本
        if (!cancelRef.current) setHtml(null);
      });

    return () => {
      cancelRef.current = true;
    };
  }, [code, lang, isStreaming, appTheme]);

  return (
    <div className="md-code-shell">
      {html ? (
        <div className="shiki-block" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre className="md-code-block">
          <code>{code}</code>
        </pre>
      )}
      {!isStreaming && <CopyCodeButton code={code} />}
    </div>
  );
}

// ─── Pre 透传（避免 react-markdown 在 CodeBlock 外套两层 <pre>）───────────────

function PreWrapper({ children }: ComponentPropsWithoutRef<'pre'>) {
  return <>{children}</>;
}

// ─── components map（模块级常量，不随 render 重建）─────────────────────────────

const REMARK_PLUGINS = [remarkGfm];

const MD_COMPONENTS: Components = {
  // 行内代码 vs 块级代码的分流判断：
  //   - className 含 language-* → 带语言声明的围栏代码块
  //   - children 含换行        → 无语言声明的围栏代码块
  //   - 其余                   → 行内 `code`（github-markdown-css 负责样式）
  code({ children, className, ...rest }) {
    const isBlock =
      !!className?.startsWith('language-') || String(children).includes('\n');
    return isBlock ? (
      <CodeBlock className={className} {...rest}>
        {children}
      </CodeBlock>
    ) : (
      <code className={className} {...rest}>
        {children}
      </code>
    );
  },
  pre: PreWrapper,
};

// ─── 导出组件 ─────────────────────────────────────────────────────────────────

type Props = {
  content: string;
  /** 传入 true 时启用打字机动画；false（默认）直接渲染完整内容 */
  isStreaming?: boolean;
};

export default function MarkdownContent({ content, isStreaming = false }: Props): JSX.Element {
  // 打字机动画：让 displayed 以 60fps 平滑追赶 content
  const displayed = useTypewriter(content, isStreaming);

  // 流式期间修复可能截断的 Markdown 语法（主要处理未闭合的代码围栏）
  const renderText = isStreaming ? patchStreamingMarkdown(displayed) : displayed;

  return (
    <StreamingContext.Provider value={isStreaming}>
      <div className="markdown-body">
        <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS}>
          {renderText}
        </ReactMarkdown>
        {/* 流式光标：追赶结束前不显示（没内容可追赶时也不显示）*/}
        {isStreaming && <span className="streaming-cursor" aria-hidden>▋</span>}
      </div>
    </StreamingContext.Provider>
  );
}
