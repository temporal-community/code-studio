/** Contract of agent_dag.workspace_agent.WorkspacePythonAgentWorkflow. */
export interface WorkspacePythonAgent {
  initData: null;
  handlers: { generate: { input: { text: string }; output: { text: string } } };
  states: Record<never, never>;
}
