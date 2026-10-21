import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const { createThemeController } = await import(pathToFileURL(process.env.THEME_MODULE).href);

function harness({ stored = null, systemDark = false, readError = false, writeError = false } = {}) {
  let systemListener;
  const applied = [];
  const writes = [];
  const controller = createThemeController({
    readPreference() { if (readError) throw new Error('storage disabled'); return stored; },
    writePreference(mode) { if (writeError) throw new Error('storage disabled'); writes.push(mode); stored = mode; },
    getSystemDark: () => systemDark,
    subscribeSystem(listener) { systemListener = listener; return () => { systemListener = undefined; }; },
    apply: mode => applied.push(mode),
  });
  return {
    controller, applied, writes,
    setSystem(dark) { systemDark = dark; systemListener?.(dark); },
    get listenerActive() { return !!systemListener; },
    get stored() { return stored; },
  };
}

test('system preference is the initial mode and is followed until the user selects a mode', () => {
  const app = harness({ systemDark: true });
  assert.deepEqual(app.controller.initialize(), { mode: 'dark', preference: null });
  assert.equal(app.listenerActive, true);
  app.setSystem(false);
  assert.equal(app.controller.getState().mode, 'light');
  assert.deepEqual(app.applied, ['dark', 'light']);

  app.controller.setTheme('dark');
  assert.deepEqual(app.controller.getState(), { mode: 'dark', preference: 'dark' });
  assert.equal(app.listenerActive, false);
  app.setSystem(false);
  assert.equal(app.controller.getState().mode, 'dark');
  assert.deepEqual(app.writes, ['dark']);
});

test('valid saved selection overrides system preference across controller initialization', () => {
  const first = harness({ stored: 'light', systemDark: true });
  assert.deepEqual(first.controller.initialize(), { mode: 'light', preference: 'light' });
  assert.equal(first.listenerActive, false);
  first.controller.setTheme('dark');
  const reloaded = harness({ stored: first.stored, systemDark: false });
  assert.deepEqual(reloaded.controller.initialize(), { mode: 'dark', preference: 'dark' });
});

test('invalid or unavailable storage falls back to system preference without blocking switching', () => {
  const invalid = harness({ stored: 'sepia', systemDark: true });
  assert.deepEqual(invalid.controller.initialize(), { mode: 'dark', preference: null });
  assert.equal(invalid.listenerActive, true);
  invalid.controller.setTheme('light');
  assert.equal(invalid.stored, 'light');

  const unavailable = harness({ systemDark: true, readError: true, writeError: true });
  assert.deepEqual(unavailable.controller.initialize(), { mode: 'dark', preference: null });
  assert.deepEqual(unavailable.controller.toggle(), { mode: 'light', preference: 'light' });
  assert.equal(unavailable.controller.getState().mode, 'light');
});


test('theme toggle exposes the active-mode icon and accessible icon-only button', async () => {
  const component = await readFile(new URL('../ui/shared/ThemeToggle.svelte', import.meta.url), 'utf8');
  assert.match(component, /aria-label="Toggle color theme"/);
  assert.match(component, /aria-pressed=\{mode === 'dark'\}/);
  assert.match(component, /mode === 'light'[\s\S]*?class="theme-icon sun"/);
  assert.match(component, /:else}[\s\S]*?class="theme-icon moon"/);
  assert.doesNotMatch(component, /Use (?:light|dark) mode/);
});

test('theme icon transitions are mode-change only, directional, and reduced-motion aware', async () => {
  const component = await readFile(new URL('../ui/shared/ThemeToggle.svelte', import.meta.url), 'utf8');
  assert.match(component, /if \(state\.mode !== observedMode\)/);
  assert.match(component, /@keyframes sun-rise[\s\S]*?translateY\(7px\)[\s\S]*?to\s*\{\s*opacity:\s*1;\s*transform:\s*translateY\(0\)/);
  assert.match(component, /@keyframes sunrise-color[\s\S]*?var\(--color-content-warning\)[\s\S]*?var\(--color-content-brand\)/);
  assert.match(component, /class="theme-toggle studio-button studio-button--quiet"/);
  assert.match(component, /@keyframes moon-lower[\s\S]*?translateY\(-7px\)[\s\S]*?to\s*\{\s*opacity:\s*1;\s*transform:\s*translateY\(0\)/);
  assert.match(component, /@keyframes moon-crescent[\s\S]*?translateX\(7px\)[\s\S]*?translateX\(0\)/);
  assert.match(component, /@media \(prefers-reduced-motion: reduce\)/);
});


test('dark-mode moon is initially a crescent and animates from full to crescent', async () => {
  const component = await readFile(new URL('../ui/shared/ThemeToggle.svelte', import.meta.url), 'utf8');
  assert.match(component, /\.moon-cutout \{ fill: var\(--panel-2\); transform: translateX\(0\); \}/);
  assert.match(component, /@keyframes moon-crescent\s*\{\s*from\s*\{\s*transform:\s*translateX\(7px\);\s*\}\s*to\s*\{\s*transform:\s*translateX\(0\);\s*\}\s*\}/);
});


test('shared shell uses semantic light/dark tokens and native controls with visible state', async () => {
  const css = await readFile(new URL('../ui/style.css', import.meta.url), 'utf8');
  const html = await readFile(new URL('../ui/index.html', import.meta.url), 'utf8');
  const work = await readFile(new URL('../ui/Work.svelte', import.meta.url), 'utf8');
  const sidebar = await readFile(new URL('../ui/Sidebar.svelte', import.meta.url), 'utf8');
  assert.match(css, /:root, :root\[data-theme="dark"\][\s\S]*?--color-background-primary/);
  assert.match(css, /:root\[data-theme="light"\][\s\S]*?--color-background-primary/);
  assert.match(css, /--bg:var\(--color-background-primary\)/);
  assert.match(css, /\.studio-button--primary:hover/);
  assert.match(css, /\.studio-button--primary:active/);
  assert.match(css, /\.studio-button:disabled/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(html, /family=Inter[^"']*Noto\+Sans\+Mono/);
  assert.match(work, /class="active" href="\?view=work" aria-current="page"/);
  assert.match(work, /disabled=\{!joined \|\| filesDirty \|\| busy \|\| suggesting\}/);
  assert.match(work, /class="primary studio-button studio-button--primary" aria-busy=\{busy\}/);
  assert.match(work, /button:not\(\.studio-button\)\{border:1px solid var\(--line-2\)[^}]*background:var\(--panel-2\)/);
  assert.doesNotMatch(work, /(?:^|})button\{[^}]*background:var\(--panel-2\)/);
  assert.match(work, /\.primary:not\(\.studio-button--primary\)\{background:var\(--accent\)/);
  assert.doesNotMatch(work, /\.primary\{background:var\(--accent\)/);
  assert.match(sidebar, /aria-current=\{s\.workflow_id === current \? "page" : undefined\}/);
  assert.doesNotMatch(sidebar, /outline:\s*none/);
});
