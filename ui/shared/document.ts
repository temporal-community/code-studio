/** Replicated character sequence; protocol mirrors workspace/document.py. */
export interface Character { id: string; after: string; value: string; deleted: boolean }
export interface Operation { inserts: Omit<Character, "deleted">[]; deletes: string[] }
export interface Snapshot { nodes: Character[]; clock: number }
export interface TextChange { start: number; end: number; text: string }

export class SharedDocument {
  nodes = new Map<string, Character>();
  clock = 0;

  constructor(snapshot?: Snapshot) {
    if (snapshot) {
      this.nodes = new Map(snapshot.nodes.map(n => [n.id, { ...n }]));
      this.clock = snapshot.clock;
    }
  }

  visible(): Character[] {
    const children = new Map<string, string[]>();
    for (const node of this.nodes.values()) {
      const siblings = children.get(node.after) ?? [];
      siblings.push(node.id);
      children.set(node.after, siblings);
    }
    for (const siblings of children.values()) siblings.sort();
    const stack = [...(children.get("") ?? [])];
    const result: Character[] = [];
    while (stack.length) {
      const node = this.nodes.get(stack.pop()!)!;
      if (!node.deleted) result.push(node);
      stack.push(...(children.get(node.id) ?? []));
    }
    return result;
  }

  get text(): string { return this.visible().map(n => n.value).join(""); }

  apply(operation: Operation): void {
    for (const node of operation.inserts) {
      if (!this.nodes.has(node.id)) this.nodes.set(node.id, { ...node, deleted: false });
      this.clock = Math.max(this.clock, Number(node.id.split(":")[0]));
    }
    for (const id of operation.deletes) {
      const node = this.nodes.get(id);
      if (node) node.deleted = true;
    }
  }

  edit(next: string, id: string): Operation {
    const visible = this.visible(), before = visible.map(n => n.value), after = Array.from(next);
    let start = 0, endBefore = before.length, endAfter = after.length;
    while (start < endBefore && start < endAfter && before[start] === after[start]) start++;
    while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) {
      endBefore--; endAfter--;
    }
    return this.patch([{ start, end: endBefore, text: after.slice(start, endAfter).join('') }], id);
  }

  /** Apply disjoint changes to one snapshot while retaining all unchanged character IDs. */
  patch(changes: TextChange[], id: string): Operation {
    const visible = this.visible();
    const operation: Operation = { inserts: [], deletes: [] };
    const clock = String(this.clock + 1).padStart(16, "0");
    let lastEnd = 0;
    for (const { start, end, text } of changes) {
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < lastEnd || end < start || end > visible.length) {
        throw new Error('Invalid formatting changes');
      }
      lastEnd = end;
      let anchor = start ? visible[start - 1]!.id : '';
      for (const value of Array.from(text)) {
        const node = { id: `${clock}:${id}:${String(operation.inserts.length).padStart(6, '0')}`, after: anchor, value };
        operation.inserts.push(node);
        anchor = node.id;
      }
      operation.deletes.push(...visible.slice(start, end).map(node => node.id));
    }
    return operation;
  }

  /** A stable cursor anchor, converting the textarea's UTF-16 offset to Unicode characters. */
  anchor(offset: number): string {
    const count = Array.from(this.text.slice(0, offset)).length;
    return count ? (this.visible()[count - 1]?.id ?? "") : "";
  }

  offset(anchor: string): number {
    if (!anchor) return 0;
    const offsets = new Map<string, number>();
    let offset = 0;
    for (const node of this.visible()) {
      offset += node.value.length;
      offsets.set(node.id, offset);
    }
    // A removed selection anchor falls back to the closest surviving predecessor.
    while (anchor) {
      const found = offsets.get(anchor);
      if (found !== undefined) return found;
      const node = this.nodes.get(anchor);
      if (!node) return offset;
      anchor = node.after;
    }
    return 0;
  }
}
