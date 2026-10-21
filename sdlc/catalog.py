"""The same catalog drives Studio definitions and the cooperative feature lifecycle."""
from cooperative.definitions import DEFINITIONS, STAGES, files_for
from .support_catalog import support_catalog


def catalog(workspace, features, reviews):
    all_features = [features.store.get(f['id']) for f in features.store.list()]
    agents = []
    for index, key in enumerate(STAGES):
        definition = DEFINITIONS[key]
        project = workspace.agent_project_snapshot(key)
        runs = [r for f in all_features for r in f.get('pipeline', {}).get('runs', []) if r['stage'] == key]
        agents.append({'id': key, 'name': definition['name'], 'stage': definition['verb'], 'mark': f'{index + 1:02d}',
            'description': definition['description'], 'capabilities': definition['tools'],
            'inputs': definition['input'], 'outputs': definition['output'], 'files': files_for(key),
            'studio_url': '/?view=studio&agent=' + key, 'runtime': 'SdlcStageAgent', 'designer': 'StudioDesignerAgent',
            'version': project['version'], 'revision': project['revision'], 'run_count': len(runs),
            'active_count': sum(r['status'] in ('queued', 'running') for r in runs)})
    return {'agents': agents, 'support_agents': support_catalog(workspace),
            'stages': [{'name': DEFINITIONS[key]['name'], 'agent': key} for key in STAGES]}
