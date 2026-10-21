export interface SupportAgent {
  id: string;
  name: string;
  description: string;
  trigger: string;
  capabilities: string[];
  model: string | null;
  model_source: string;
  model_stage: string | null;
  model_error: string | null;
  model_settings_url: string | null;
  max_turns: number;
  usage_note: string;
  usage: {
    since: number;
    requests: number;
    running: number;
    submitted: number;
    completed: number;
    failed: number;
    interrupted: number;
  };
}

export interface SupportPrompt {
  id: string;
  label: string;
  content: string;
  default_content: string;
  version: string;
  updated_at: number | null;
  author: string | null;
}

export interface SupportAgentDetails {
  id: string;
  prompts: SupportPrompt[];
  instructions_note: string;
}
