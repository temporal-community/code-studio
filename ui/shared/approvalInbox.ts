export type ApprovalFeature = Readonly<{
  id: string;
  title: string;
  pipeline_status: string;
  current_stage: string;
}>;

export type ApprovalInboxEntry = ApprovalFeature & Readonly<{
  approvalType: 'Plan approval' | 'Review approval';
  approvalStage: 'planner' | 'review';
}>;

/** Project the current platform snapshot onto the two actionable approval gates. */
export function approvalInbox(features: readonly ApprovalFeature[]): ApprovalInboxEntry[] {
  const entries: ApprovalInboxEntry[] = [];
  for (const feature of features) {
    if (feature.pipeline_status === 'needs_plan_approval') {
      entries.push({ ...feature, approvalType: 'Plan approval', approvalStage: 'planner' });
    } else if (feature.pipeline_status === 'needs_release_approval') {
      entries.push({ ...feature, approvalType: 'Review approval', approvalStage: 'review' });
    }
  }
  return entries;
}
