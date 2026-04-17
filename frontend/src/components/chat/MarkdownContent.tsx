import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';
import type { Components } from 'react-markdown';

type Props = { content: string };

const components: Components = {
  code({ className, children, ...props }) {
    const isBlock = Boolean(className?.includes('language-'));
    if (isBlock) {
      return (
        <pre
          style={{
            margin: '8px 0',
            borderRadius: 6,
            overflow: 'auto',
            fontSize: 13,
          }}
        >
          <code className={className} {...props}>
            {children}
          </code>
        </pre>
      );
    }
    return (
      <code
        style={{
          background: 'rgba(127,127,127,0.15)',
          padding: '2px 6px',
          borderRadius: 4,
          fontSize: '0.88em',
          fontFamily: 'monospace',
        }}
        {...props}
      >
        {children}
      </code>
    );
  },
  p({ children }) {
    return <p style={{ margin: '6px 0', lineHeight: 1.7 }}>{children}</p>;
  },
  ul({ children }) {
    return <ul style={{ margin: '6px 0', paddingLeft: 20 }}>{children}</ul>;
  },
  ol({ children }) {
    return <ol style={{ margin: '6px 0', paddingLeft: 20 }}>{children}</ol>;
  },
  blockquote({ children }) {
    return (
      <blockquote
        style={{
          borderLeft: '3px solid #1677ff',
          margin: '8px 0',
          paddingLeft: 12,
          opacity: 0.75,
        }}
      >
        {children}
      </blockquote>
    );
  },
};

export default function MarkdownContent({ content }: Props): JSX.Element {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}
