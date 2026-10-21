export type ChangeKind = 'unchanged' | 'changed' | 'added' | 'removed' | 'absent';
export type EntryChange = { kind: ChangeKind; before?: any; after?: any; changes?: { field: string; before: any; after: any }[] };
export type FieldComparison = {
  key: string;
  label: string;
  leftPresent: boolean;
  rightPresent: boolean;
  leftValue?: any;
  rightValue?: any;
  kind: ChangeKind;
  entries?: EntryChange[];
};

const definitions = [
  { key: 'summary', label: 'Summary', type: 'text' },
  { key: 'deliverables', label: 'Deliverables', type: 'deliverables' },
  { key: 'acceptance_criteria', label: 'Acceptance criteria', type: 'strings' },
  { key: 'questions', label: 'Questions', type: 'strings' },
  { key: 'findings', label: 'Findings', type: 'findings' },
  { key: 'risks', label: 'Risks', type: 'strings' },
  { key: 'handoff', label: 'Handoff', type: 'text' },
] as const;

function canonical(value: any): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
const equal = (a: any, b: any) => canonical(a) === canonical(b);
function status(leftPresent: boolean, rightPresent: boolean, same: boolean): ChangeKind {
  if (!leftPresent && !rightPresent) return 'absent';
  if (!leftPresent) return 'added';
  if (!rightPresent) return 'removed';
  return same ? 'unchanged' : 'changed';
}
function listEntries(left: any[], right: any[], type: 'strings' | 'deliverables' | 'findings'): EntryChange[] {
  const leftRemaining = new Set(left.map((_, i) => i));
  const rightRemaining = new Set(right.map((_, i) => i));
  const entries: EntryChange[] = [];
  // Consume identical full entries as a multiset, preserving duplicate counts.
  for (let i = 0; i < left.length; i++) {
    const j = right.findIndex((entry, index) => rightRemaining.has(index) && equal(left[i], entry));
    if (j >= 0) {
      leftRemaining.delete(i); rightRemaining.delete(j);
      entries.push({ kind: 'unchanged', before: left[i], after: right[j] });
    }
  }
  if (type !== 'strings') {
    const identity = (entry: any): string | null => {
      if (!entry || typeof entry !== 'object') return null;
      if (type === 'deliverables') return Object.hasOwn(entry, 'title') ? canonical(entry.title) : null;
      return Object.hasOwn(entry, 'path') && Object.hasOwn(entry, 'title') ? canonical([entry.path, entry.title]) : null;
    };
    const counts = (indices: Set<number>, values: any[]) => {
      const result = new Map<string, number>();
      for (const index of indices) {
        const key = identity(values[index]);
        if (key !== null) result.set(key, (result.get(key) || 0) + 1);
      }
      return result;
    };
    const leftCounts = counts(leftRemaining, left), rightCounts = counts(rightRemaining, right);
    for (const i of [...leftRemaining]) {
      const key = identity(left[i]);
      if (key === null || leftCounts.get(key) !== 1 || rightCounts.get(key) !== 1) continue;
      const j = [...rightRemaining].find(index => identity(right[index]) === key);
      if (j === undefined) continue;
      leftRemaining.delete(i); rightRemaining.delete(j);
      const fields = type === 'deliverables' ? ['body'] : ['severity', 'line', 'explanation'];
      entries.push({ kind: 'changed', before: left[i], after: right[j], changes: fields
        .filter(field => !equal(left[i]?.[field], right[j]?.[field]))
        .map(field => ({ field, before: left[i]?.[field], after: right[j]?.[field] })) });
    }
  }
  for (const i of leftRemaining) entries.push({ kind: 'removed', before: left[i] });
  for (const j of rightRemaining) entries.push({ kind: 'added', after: right[j] });
  return entries;
}

export function compareStageResults(left: unknown, right: unknown): FieldComparison[] {
  const leftResult = left && typeof left === 'object' ? left as Record<string, any> : null;
  const rightResult = right && typeof right === 'object' ? right as Record<string, any> : null;
  return definitions.map(({ key, label, type }) => {
    const leftPresent = !!leftResult && Object.hasOwn(leftResult, key);
    const rightPresent = !!rightResult && Object.hasOwn(rightResult, key);
    const leftValue = leftPresent ? leftResult![key] : undefined;
    const rightValue = rightPresent ? rightResult![key] : undefined;
    if (type === 'text') {
      const same = leftPresent && rightPresent && equal(leftValue, rightValue);
      return { key, label, leftPresent, rightPresent, leftValue, rightValue, kind: status(leftPresent, rightPresent, same) };
    }
    const safeLeft = Array.isArray(leftValue) ? leftValue : [];
    const safeRight = Array.isArray(rightValue) ? rightValue : [];
    const entries = listEntries(safeLeft, safeRight, type);
    const same = leftPresent && rightPresent && entries.every(entry => entry.kind === 'unchanged');
    return { key, label, leftPresent, rightPresent, leftValue, rightValue, kind: status(leftPresent, rightPresent, same), entries };
  });
}
