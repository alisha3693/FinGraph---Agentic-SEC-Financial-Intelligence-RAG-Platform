// Browser-stored preferences: theme and recent prompts.

export const THEME_KEY = 'sec-intel-theme';

export const PROMPT_HISTORY_KEY = 'sec-intel-prompt-history';

export const PROMPT_HISTORY_LIMIT = 5;

export function getInitialTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    // localStorage unavailable (private mode, disabled storage) — fall through to OS setting.
  }
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function getInitialPromptHistory() {
  try {
    const stored = JSON.parse(localStorage.getItem(PROMPT_HISTORY_KEY));
    if (Array.isArray(stored)) return stored.filter((p) => typeof p === 'string').slice(0, PROMPT_HISTORY_LIMIT);
  } catch {
    // localStorage unavailable or holds malformed JSON — start with an empty history.
  }
  return [];
}
