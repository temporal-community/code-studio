export type ThemeMode = 'light' | 'dark';
export type ThemePreference = ThemeMode | null;

export interface ThemeState {
  mode: ThemeMode;
  preference: ThemePreference;
}

interface ThemeEnvironment {
  readPreference: () => string | null;
  writePreference: (mode: ThemeMode) => void;
  getSystemDark: () => boolean;
  subscribeSystem: (listener: (dark: boolean) => void) => () => void;
  apply: (mode: ThemeMode) => void;
}

const validMode = (value: string | null): value is ThemeMode => value === 'light' || value === 'dark';

export function createThemeController(environment: ThemeEnvironment) {
  let preference: ThemePreference = null;
  let mode: ThemeMode = 'light';
  let initialized = false;
  let unsubscribeSystem: (() => void) | undefined;
  const listeners = new Set<(state: ThemeState) => void>();

  const state = (): ThemeState => ({ mode, preference });
  const publish = () => listeners.forEach(listener => listener(state()));
  const apply = () => {
    environment.apply(mode);
    publish();
  };
  const followSystem = (dark: boolean) => {
    if (preference !== null) return;
    mode = dark ? 'dark' : 'light';
    apply();
  };

  function initialize(): ThemeState {
    unsubscribeSystem?.();
    unsubscribeSystem = undefined;
    let stored: string | null = null;
    try { stored = environment.readPreference(); } catch { /* Storage is optional. */ }
    preference = validMode(stored) ? stored : null;
    let systemDark = false;
    try { systemDark = environment.getSystemDark(); } catch { /* Default to the light palette. */ }
    mode = preference ?? (systemDark ? 'dark' : 'light');
    initialized = true;
    apply();
    if (preference === null) {
      try { unsubscribeSystem = environment.subscribeSystem(followSystem); } catch { /* Keep the resolved initial mode. */ }
    }
    return state();
  }

  function setTheme(next: ThemeMode): ThemeState {
    if (!validMode(next)) return state();
    if (!initialized) initialize();
    preference = next;
    unsubscribeSystem?.();
    unsubscribeSystem = undefined;
    try { environment.writePreference(next); } catch { /* Current-page switching still works. */ }
    mode = next;
    apply();
    return state();
  }

  return {
    initialize,
    setTheme,
    toggle: () => setTheme(mode === 'dark' ? 'light' : 'dark'),
    getState: state,
    subscribe(listener: (state: ThemeState) => void) {
      listeners.add(listener);
      listener(state());
      return () => listeners.delete(listener);
    },
  };
}

export const theme = createThemeController({
  readPreference: () => localStorage.getItem('code-studio:theme'),
  writePreference: mode => localStorage.setItem('code-studio:theme', mode),
  getSystemDark: () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  subscribeSystem: listener => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const change = (event: MediaQueryListEvent) => listener(event.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  },
  apply: mode => {
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', mode);
  },
});
