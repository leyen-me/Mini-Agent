import { Play } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, errorMessage } from '../api/client';
import type { CommandInfo } from '../api/types';
import { useUi } from '../state/UiContext';
import { Field } from '@/components/common/Field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Modal } from './Modal';

interface Props {
  command: CommandInfo | null;
  initialArgs?: string;
  onClose: () => void;
  onRun: (command: CommandInfo, prompt: string) => Promise<void>;
  onToast: (message: string, kind?: 'info' | 'error' | 'success') => void;
}

export function CommandPreview({
  command,
  initialArgs = '',
  onClose,
  onRun,
  onToast,
}: Props) {
  const { t } = useUi();
  const [args, setArgs] = useState('');
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    setArgs(initialArgs);
    setPrompt(command?.promptTemplate ?? '');
  }, [command, initialArgs]);

  useEffect(() => {
    if (!command) return;
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api.commands
        .resolve(`/${command.name}${args ? ` ${args}` : ''}`)
        .then((result) => {
          if (!cancelled) setPrompt(result.prompt);
        })
        .catch((error) => {
          if (!cancelled) setPrompt(errorMessage(error));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [args, command]);

  const run = async () => {
    if (!command || running) return;
    setRunning(true);
    try {
      await onRun(command, prompt);
      onClose();
    } catch (error) {
      onToast(errorMessage(error), 'error');
    } finally {
      setRunning(false);
    }
  };

  return (
    <Modal
      open={Boolean(command)}
      title={`/${command?.name ?? ''}`}
      description={command ? command.description || t('chat.customCommandHint') : undefined}
      onClose={onClose}
      footer={
        command && (
          <>
            <Button variant="ghost" onClick={onClose}>
              {t('app.cancel')}
            </Button>
            <Button onClick={() => void run()} disabled={!prompt.trim() || running}>
              <Play /> {t('chat.runCommand')}
            </Button>
          </>
        )
      }
    >
      {command && (
        <div className="flex flex-col gap-6">
          <Field label={t('chat.commandArguments')}>
            <Input
              autoFocus
              value={args}
              onChange={(event) => setArgs(event.target.value)}
              placeholder={t('chat.commandArgumentsHint')}
            />
          </Field>
          <section className="command-prompt-preview">
            <header>
              <span>{t('chat.commandPreview')}</span>
              {loading && <span>{t('app.loading')}…</span>}
            </header>
            <pre>{prompt}</pre>
          </section>
        </div>
      )}
    </Modal>
  );
}
