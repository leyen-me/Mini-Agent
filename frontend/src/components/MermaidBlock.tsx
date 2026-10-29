import { useEffect, useId, useRef, useState } from 'react';
import { useUi } from '../state/UiContext';

let mermaidPromise: Promise<typeof import('mermaid').default> | null = null;

async function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((module) => {
      module.default.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme:
          document.documentElement.dataset.theme === 'light'
            ? 'default'
            : 'dark',
        fontFamily: 'inherit',
      });
      return module.default;
    });
  }
  return mermaidPromise;
}

export function MermaidBlock({ code }: { code: string }) {
  const { t } = useUi();
  const rawId = useId();
  const id = `mermaid-${rawId.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');
  const container = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = container.current;
    let cancelled = false;
    setError('');
    setSvg('');
    loadMermaid()
      .then((mermaid) => mermaid.render(id, code))
      .then((result) => {
        if (!cancelled) setSvg(result.svg);
      })
      .catch((reason: unknown) => {
        if (!cancelled)
          setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
      if (node) node.innerHTML = '';
    };
  }, [code, id]);

  if (error) {
    return (
      <div className="mermaid-error my-4 border border-destructive/40 bg-danger-soft p-4 text-[13px] text-destructive" role="alert">
        <strong>{t('chat.mermaid.error')}</strong>
        <pre className="mt-2 font-mono text-[12px] whitespace-pre-wrap">{code}</pre>
        <details>
          <summary>{t('chat.mermaid.details')}</summary>
          <code>{error}</code>
        </details>
      </div>
    );
  }
  return (
    <div className="mermaid-block my-4 overflow-auto border border-border bg-background p-4" ref={container}>
      {svg ? (
        <div
          className="mermaid-svg"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : (
        <div className="mermaid-loading font-mono text-[12px] text-muted-foreground" role="status">
          {t('chat.mermaid.rendering')}
        </div>
      )}
    </div>
  );
}
