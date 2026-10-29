const PREFIX = 'mini-agent-draft:';

export function loadDraft(sessionId: string | null): string {
  try {
    return localStorage.getItem(PREFIX + (sessionId ?? '__new__')) ?? '';
  } catch {
    return '';
  }
}

export function saveDraft(sessionId: string | null, content: string): void {
  try {
    const key = PREFIX + (sessionId ?? '__new__');
    if (content) localStorage.setItem(key, content);
    else localStorage.removeItem(key);
  } catch {
    // localStorage can be unavailable in private mode
  }
}

export function clearDraft(sessionId: string | null): void {
  saveDraft(sessionId, '');
}
