import { isValidElement, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import rehypeSanitize from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import { CopyButton } from './CopyButton';
import { MermaidBlock } from './MermaidBlock';
import { useUi } from '../state/UiContext';

function nodeText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join('');
  if (isValidElement<{ children?: ReactNode }>(node))
    return nodeText(node.props.children);
  return '';
}

export function Markdown({ children }: { children: string }) {
  const { t } = useUi();
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeSanitize, rehypeKatex, rehypeHighlight]}
        components={{
          a: ({ children: linkChildren, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener">
              {linkChildren}
            </a>
          ),
          pre: ({ children: preChildren }) => {
            const text = nodeText(preChildren);
            return (
              <div className="code-block group/code">
                <CopyButton
                  text={text}
                  label={t('app.copyCode')}
                  iconOnly
                  className="absolute top-2 right-2 bg-white/5 text-neutral-400 opacity-0 group-hover/code:opacity-100 hover:bg-white/15 hover:text-white focus-visible:opacity-100"
                />
                <pre>{preChildren}</pre>
              </div>
            );
          },
          code: ({ className, children: codeChildren, ...props }) => {
            const language = /language-([\w-]+)/.exec(className ?? '')?.[1];
            const text = nodeText(codeChildren).replace(/\n$/, '');
            if (language === 'mermaid') return <MermaidBlock code={text} />;
            return (
              <code className={className} {...props}>
                {codeChildren}
              </code>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
