const key = 'code-studio:feature-draft:v1';
const defaults = { directory: '', title: '', idea: '', brief: '', acceptance: '', branch: '', sourceMode: 'current', base: 'HEAD' };
export type FeatureDraft = typeof defaults;

export function loadFeatureDraft(): FeatureDraft {
  const draft = { ...defaults };
  try {
    const saved = JSON.parse(localStorage.getItem(key) || 'null');
    if (saved && typeof saved === 'object') {
      for (const field of Object.keys(defaults) as (keyof FeatureDraft)[]) {
        if (typeof saved[field] === 'string') draft[field] = saved[field];
      }
      if (!['current', 'git'].includes(draft.sourceMode)) draft.sourceMode = 'current';
    }
  } catch { /* A missing, invalid, or unavailable store should not block the form. */ }
  return draft;
}

export function saveFeatureDraft(draft: FeatureDraft): void {
  try { localStorage.setItem(key, JSON.stringify(draft)); }
  catch { /* Keep the form usable when browser storage is unavailable. */ }
}

// Deleting a saved draft resets the form, but must not replace separate recovery data.
// Suppress exactly the autosave caused by that reset; later edits resume normal saving.
export function createFeatureDraftRecoveryAutosave() {
  let preserveNextSave = false;
  return {
    preserveNextSave() { preserveNextSave = true; },
    save(draft: FeatureDraft) {
      if (preserveNextSave) {
        preserveNextSave = false;
        return;
      }
      saveFeatureDraft(draft);
    },
  };
}

export function clearFeatureDraft(): void {
  try { localStorage.removeItem(key); } catch { /* Storage may be unavailable. */ }
}

const draftsKey = 'code-studio:saved-feature-drafts:v1';
export type SavedFeatureDraft = FeatureDraft & { id: string; updatedAt: number };

function parseFeatureDrafts(value: string | null): SavedFeatureDraft[] {
  const parsed = JSON.parse(value || '[]');
  if (!Array.isArray(parsed)) throw new Error('Saved feature drafts are not a list');
  return parsed.map(item => item && typeof item === 'object' && item.idea === undefined ? { ...item, idea: '' } : item)
    .filter(item => item && typeof item.id === 'string' &&
    typeof item.updatedAt === 'number' && Object.keys(defaults).every(field => typeof item[field] === 'string'))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function listFeatureDrafts(): SavedFeatureDraft[] {
  try { return parseFeatureDrafts(localStorage.getItem(draftsKey)); }
  catch { return []; }
}

export function storeFeatureDraft(id: string, form: FeatureDraft): SavedFeatureDraft[] {
  const drafts = [{ ...form, id, updatedAt: Date.now() }, ...listFeatureDrafts().filter(item => item.id !== id)];
  // Explicit saves must report failures so the user never mistakes a failed save for success.
  localStorage.setItem(draftsKey, JSON.stringify(drafts));
  return drafts;
}

export function removeFeatureDraft(id: string): SavedFeatureDraft[] {
  // Unlike the display-oriented list helper, deletion must not mistake unreadable
  // storage for an empty list and overwrite the user's saved drafts.
  const drafts = parseFeatureDrafts(localStorage.getItem(draftsKey)).filter(item => item.id !== id);
  localStorage.setItem(draftsKey, JSON.stringify(drafts));
  return drafts;
}
