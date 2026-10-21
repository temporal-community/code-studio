const key = 'code-studio:name';
let currentName = '';

// Session-only display identity. This is not authentication or authorization.
export function sessionName(): string {
  if (currentName) return currentName;
  try {
    currentName = (sessionStorage.getItem(key)?.trim() || sessionStorage.getItem('feature-name')?.trim() || '').slice(0, 40);
  } catch { /* Storage may be unavailable; keep this page's identity in memory. */ }
  return currentName;
}

export function joinSession(value: string): boolean {
  const name = value.trim().slice(0, 40);
  if (!name) return false;
  currentName = name;
  try {
    sessionStorage.setItem(key, name);
    sessionStorage.removeItem('feature-name');
  } catch { /* The current page still works without browser storage. */ }
  return true;
}
