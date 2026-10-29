import { Target } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Goal } from '../api/types';
import { useUi } from '../state/UiContext';
import { Field } from '@/components/common/Field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Modal } from './Modal';

interface Props {
  open: boolean;
  goal: Goal | null;
  onClose: () => void;
  onSave: (body: {
    objective: string;
    criteria: string;
    tasks: Array<{
      title: string;
      status: 'pending' | 'in_progress' | 'completed' | 'blocked';
    }>;
  }) => Promise<void>;
}

export function GoalDialog({ open, goal, onClose, onSave }: Props) {
  const { t } = useUi();
  const [objective, setObjective] = useState('');
  const [criteria, setCriteria] = useState('');
  const [tasks, setTasks] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setObjective(goal?.objective ?? '');
    setCriteria(goal?.criteria ?? '');
    setTasks(
      goal?.tasks
        .map((task) => {
          const marker =
            task.status === 'completed'
              ? '[x] '
              : task.status === 'in_progress'
                ? '[>] '
                : task.status === 'blocked'
                  ? '[!] '
                  : '';
          return `${marker}${task.title}`;
        })
        .join('\n') ?? '',
    );
    setError(null);
  }, [open, goal]);

  const submit = async () => {
    if (!objective.trim() || !criteria.trim()) {
      setError(t('chat.goal.required'));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const parsed = tasks
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const done = /^\[[xX]\]\s*/.test(line);
          const active = /^\[>\]\s*/.test(line);
          const blocked = /^\[[!]\]\s*/.test(line);
          return {
            title: line.replace(/^\[[xX!>]\]\s*/, ''),
            status: (done
              ? 'completed'
              : active
                ? 'in_progress'
                : blocked
                  ? 'blocked'
                  : 'pending') as
              'completed' | 'in_progress' | 'blocked' | 'pending',
          };
        });
      await onSave({
        objective: objective.trim(),
        criteria: criteria.trim(),
        tasks: parsed,
      });
      onClose();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={goal ? t('chat.goal.title') : t('chat.goal.set')}
      onClose={onClose}
      description={t('inspector.noGoalHint')}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('app.cancel')}
          </Button>
          <Button disabled={saving} onClick={() => void submit()}>
            <Target /> {t('chat.goal.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-6">
        <Field label={t('chat.goal.objective')}>
          <Input
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
            placeholder={t('chat.goal.objective')}
          />
        </Field>
        <Field label={t('chat.goal.criteria')}>
          <Textarea
            rows={3}
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
            placeholder={t('chat.goal.criteria')}
          />
        </Field>
        <Field label={t('chat.goal.tasks')} hint={t('chat.goal.taskPlaceholder')}>
          <Textarea
            rows={6}
            value={tasks}
            onChange={(event) => setTasks(event.target.value)}
            placeholder={t('chat.goal.taskPlaceholder')}
            className="font-mono text-[13px]"
          />
        </Field>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
