"""Inspect support agents using the same prompts and limits as their runtimes."""
from cooperative.definitions import DEFINITIONS, STAGES, files_for
from cooperative.models import StageSettings

SUPPORT_AGENTS = {
    'studio-designer': {
        'name': 'Studio designer',
        'description': 'Helps you customize a workflow agent’s instructions, settings, and Python flow.',
        'trigger': 'When you send a message in an Agent Studio.',
        'model_source': 'Fixed for the Studio assistant',
        'model_stage': None,
        'capabilities': ['Read agent definitions', 'Edit definitions', 'Validate changes'],
        'usage_note': 'Submitted messages are acknowledged requests, not completed model turns. Retries of the same message count once.',
    },
    'improvement-advisor': {
        'name': 'Feature suggestion agent',
        'description': 'Turns a short idea into a feature brief, or suggests a next feature from the selected source and existing features.',
        'trigger': 'When you choose “Suggest Feature” or submit an idea in “Describe feature”.',
        'model_source': 'Uses Ideation’s model setting',
        'model_stage': 'ideation',
        'capabilities': ['Inspect supplied context', 'Suggest a feature'],
        'usage_note': 'Each request asks for one suggestion. Completed requests include a validated response.',
    },
    'merge-resolver': {
        'name': 'Merge conflict resolver',
        'description': 'Proposes a resolution that preserves the intent of both local and feature changes.',
        'trigger': 'When you choose “Resolve with agent and merge” for a conflict.',
        'model_source': 'Uses Development’s model setting',
        'model_stage': 'development',
        'capabilities': ['Compare file versions', 'Propose a resolution'],
        'usage_note': 'Requests are counted per conflicting file. A completed response can still report blocked; it does not mean the merge was applied.',
    },
}


def support_catalog(workspace):
    from agent_dag.studio_designer import MODEL, MAX_TURNS as STUDIO_TURNS
    from agent_dag.improvement_suggestion import MAX_TURNS as SUGGESTION_TURNS
    from agent_dag.merge_resolution import MAX_TURNS as MERGE_TURNS
    limits = {'studio-designer': STUDIO_TURNS, 'improvement-advisor': SUGGESTION_TURNS, 'merge-resolver': MERGE_TURNS}
    result = []
    for key, definition in SUPPORT_AGENTS.items():
        stage = definition['model_stage']
        model, model_error = MODEL, None
        if stage:
            project = workspace.agent_project_snapshot(stage)
            try:
                model = StageSettings.model_validate_json(project['files'][files_for(stage)[2]]).model
            except ValueError:
                model, model_error = None, f"Fix {DEFINITIONS[stage]['name']}’s settings to use this agent."
        result.append({'id': key, **definition, 'model': model, 'model_error': model_error,
                       'model_settings_url': '/?view=studio&agent=' + stage if stage else None,
                       'max_turns': limits[key], 'usage': workspace.support_activity.summary(key)})
    return result


def support_details(agent_id, workspace=None):
    if agent_id not in SUPPORT_AGENTS:
        raise ValueError('Unknown support agent')
    if agent_id == 'studio-designer':
        from agent_dag.studio_designer import designer_instructions
        prompts = [{'id': stage, 'label': DEFINITIONS[stage]['name'], 'content': designer_instructions(stage)} for stage in STAGES]
    elif agent_id == 'improvement-advisor':
        from agent_dag.improvement_suggestion import INSTRUCTIONS
        prompts = [{'id': 'default', 'label': 'Instructions', 'content': INSTRUCTIONS}]
    else:
        from agent_dag.merge_resolution import INSTRUCTIONS
        prompts = [{'id': 'default', 'label': 'Instructions', 'content': INSTRUCTIONS}]
    if workspace is not None:
        prompts = [{**prompt, **workspace.support_instructions.snapshot(agent_id, prompt['id'], prompt['content'])}
                   for prompt in prompts]
    return {'id': agent_id, 'prompts': prompts,
            'instructions_note': 'Saved instructions apply to new requests across this workspace. Requests already submitted keep their captured instructions. Model settings inherited from a workflow agent do not inherit that agent’s prompt.'}
