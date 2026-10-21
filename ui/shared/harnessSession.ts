type Pipeline = { execution?: { workflow_id?: string } };

// The coordinator owns the delegation history across all stages. Never fall
// back to a stage child or an older execution when no coordinator is available.
export function currentHarnessSession(pipeline?: Pipeline | null): string {
  return pipeline?.execution?.workflow_id || '';
}
