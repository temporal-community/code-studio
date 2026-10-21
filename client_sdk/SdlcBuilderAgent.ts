/** Contract of agent_dag.sdlc_builder.SdlcBuilderAgentWorkflow. */
export interface SdlcBuilderAgent {
  initData: null;
  handlers: { generate: { input: { text: string }; output: { text: string } } };
  states: Record<never, never>;
}
