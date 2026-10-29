import {
  Archive,
  CornerDownLeft,
  Download,
  MessageSquare,
  Moon,
  Play,
  Plus,
  Settings,
  Square,
  TerminalSquare,
  type LucideIcon,
} from 'lucide-react';
import { useMemo } from 'react';
import type { Session } from '../api/types';
import { useUi } from '../state/UiContext';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Kbd } from '@/components/ui/kbd';

interface Props {
  open: boolean;
  sessions: Session[];
  onClose: () => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onOpenSettings: () => void;
  onToggleTheme: () => void;
  onStop: () => void;
  onResume: () => void;
  onCompact: () => void;
  onExport: () => void;
  onFocusComposer: () => void;
}

interface Item {
  id: string;
  label: string;
  icon: LucideIcon;
  run: () => void;
}

export function CommandPalette({
  open,
  sessions,
  onClose,
  onSelectSession,
  onNewSession,
  onOpenSettings,
  onToggleTheme,
  onStop,
  onResume,
  onCompact,
  onExport,
  onFocusComposer,
}: Props) {
  const { t } = useUi();

  const groups = useMemo<Array<[string, Item[]]>>(
    () => [
      [
        t('palette.actions'),
        [
          { id: 'new', label: t('palette.newSession'), icon: Plus, run: onNewSession },
          { id: 'settings', label: t('palette.openSettings'), icon: Settings, run: onOpenSettings },
          { id: 'theme', label: t('palette.toggleTheme'), icon: Moon, run: onToggleTheme },
          { id: 'stop', label: t('palette.stop'), icon: Square, run: onStop },
          { id: 'resume', label: t('palette.resume'), icon: Play, run: onResume },
          { id: 'compact', label: t('palette.compact'), icon: Archive, run: onCompact },
          { id: 'export', label: t('palette.exportJson'), icon: Download, run: onExport },
        ],
      ],
      [
        t('palette.navigation'),
        [
          {
            id: 'focus',
            label: t('palette.focusComposer'),
            icon: TerminalSquare,
            run: onFocusComposer,
          },
        ],
      ],
      [
        t('palette.sessions'),
        sessions.slice(0, 30).map((session) => ({
          id: `session-${session.id}`,
          label: session.title || session.id,
          icon: MessageSquare,
          run: () => onSelectSession(session.id),
        })),
      ],
    ],
    [
      t,
      sessions,
      onNewSession,
      onOpenSettings,
      onToggleTheme,
      onStop,
      onResume,
      onCompact,
      onExport,
      onFocusComposer,
      onSelectSession,
    ],
  );

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        showCloseButton={false}
        aria-describedby={undefined}
        aria-label={t('app.commandPalette')}
        className="top-[14vh] translate-y-0 gap-0 overflow-hidden border-border p-0 sm:max-w-[640px]"
      >
        <DialogTitle className="sr-only">{t('app.commandPalette')}</DialogTitle>
        <Command className="bg-background **:data-[slot=command-input-wrapper]:h-14 **:data-[slot=command-input-wrapper]:px-4 **:data-[slot=command-input-wrapper]:pr-16">
          <div className="relative">
            <CommandInput
              placeholder={t('palette.placeholder')}
              aria-label={t('palette.placeholder')}
              className="h-14 text-[15px]"
            />
            <Kbd className="absolute top-1/2 right-4 -translate-y-1/2">Esc</Kbd>
          </div>
          <CommandList className="max-h-[min(52vh,440px)] py-2">
            <CommandEmpty className="py-10 text-center text-[13px] text-muted-foreground">
              {t('palette.noResults')}
            </CommandEmpty>
            {groups.map(([group, items]) =>
              items.length ? (
                <CommandGroup
                  key={group}
                  heading={group}
                  className="px-2 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-2 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-[10.5px] [&_[cmdk-group-heading]]:tracking-[0.12em] [&_[cmdk-group-heading]]:uppercase"
                >
                  {items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <CommandItem
                        key={item.id}
                        value={`${item.label} ${item.id}`}
                        onSelect={() => {
                          item.run();
                          onClose();
                        }}
                        className="group h-10 gap-3 px-3 text-[13.5px] data-[selected=true]:bg-accent"
                      >
                        <Icon className="size-4 text-muted-foreground group-data-[selected=true]:text-foreground" />
                        <span className="truncate">{item.label}</span>
                        <CornerDownLeft className="ml-auto size-3.5 opacity-0 group-data-[selected=true]:opacity-60" />
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              ) : null,
            )}
          </CommandList>
          <footer className="flex items-center justify-between gap-3 border-t border-border bg-muted/60 px-4 py-2.5 text-[11.5px] text-muted-foreground">
            <span>{t('palette.hint')}</span>
            <span className="font-mono tracking-wider uppercase">{t('app.name')}</span>
          </footer>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
