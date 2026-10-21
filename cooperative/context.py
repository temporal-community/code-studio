"""Compact stage inputs with explicit pointers to complete, immutable evidence."""
import json
from copy import deepcopy
from pathlib import Path
from .definitions import STAGES
from .design_system import POLICY_PATH, PREFIX


def upstream_index(feature, stage=None):
    latest = {}
    positions = {}
    for position, run in enumerate(feature['pipeline']['runs']):
        if run.get('result') and run['status'] == 'completed' and run['input_revision'] == feature['input_revision']:
            result = run['result']
            positions[run['stage']] = (position, result)
            latest[run['stage']] = {'summary': result['summary'][:1500], 'verdict': result['verdict'],
                'questions': result['questions'], 'changed_files': result['changed_files'],
                'artifact': f"/artifacts/scope-{run['input_revision']}/{run['stage']}/{run['id']}/result.json"}
    detailed = set()
    if stage in STAGES and stage != 'ideation':
        detailed.add(STAGES[STAGES.index(stage) - 1])
    # Review is the immediate predecessor on the repair path, while Design
    # remains the implementation contract. Do not revive superseded findings.
    if stage == 'development' and positions.get('review', (-1,))[0] > positions.get('development', (-1,))[0]:
        detailed.add('review')
    for previous in detailed & latest.keys():
        latest[previous].update(deepcopy(positions[previous][1]))
        latest[previous]['included_in_context'] = True
    return latest


def focused_repository(git, feature, stage):
    paths = git.files(feature)
    latest = {}
    for run in feature['pipeline']['runs']:
        if run.get('result') and run['status'] == 'completed' and run['input_revision'] == feature['input_revision']:
            latest[run['stage']] = run['result']
    focus = json.dumps(latest) + feature['description'] + feature.get('acceptance', '')
    changed = {path for result in latest.values() for path in result['changed_files']}
    # No alphabetical source survey. Exact upstream path references guide inspection.
    selected = [path for path in paths if path in changed or path in focus]
    if stage == 'ideation':
        selected = []
    selected = sorted(selected, key=lambda path: (path not in changed, path))
    selected += [p for p in paths if Path(p).name.lower() in ('readme.md', 'package.json', 'pyproject.toml') and p not in selected]
    excerpts = []; budget = 5000 if stage in ('ideation', 'deployment') else 14000
    for path in selected[:8]:
        if budget <= 0: break
        try: file = git.read(feature, path)
        except ValueError: continue
        content = file['content'][:min(3500, budget)]
        budget -= len(content)
        excerpts.append({**file, 'content': content, 'truncated': len(content) < len(file['content'])})
    return {'base_sha': feature['base_sha'], 'paths': paths[:500], 'file_count': len(paths), 'excerpts': excerpts,
        'note': 'Relevant excerpts only. Use source search, range reads or full reads for additional evidence; truncated excerpts are not complete files.'}


def optimize_context(feature, run, git, playbook):
    context = run['context']
    context['optimization_version'] = 2
    context['source_patch_version'] = 1
    from .browser_preview import supported
    browser = run['stage'] in ('development', 'review') and supported(feature)
    if browser: context['browser_preview_version'] = 1
    context['manual_approvals'] = [{**a, 'current': a['source_hash'] == context.get('source_hash')}
        for a in feature['pipeline'].get('manual_approvals', []) if a['revision'] == feature['input_revision']]
    context['validation_capabilities'] = {
        'rendered_browser': browser,
        'browser_scope': 'Read-only built frontend against live backend data. No submissions, edits, or new backend behavior.' if browser else 'No preview adapter configured.',
        'browser_tests': 'Legacy name: runs npm run test:collaboration. This is not rendered browser validation.',
        'recovery': 'Leave the stage blocked if you cannot recommend approval. The user may choose Manual Approve without evidence.'}
    context['validation_policy'] = (
        'Plan and validate using the tools actually available for this run. Do not invent approval requirements '
        'for browser visual testing or other unavailable capabilities, including suggestions in earlier agent handoffs. '
        'Browser visual testing is not required by this workflow. Use source inspection and available automated '
        'checks to assess UI changes; never claim they establish rendered visual correctness. '
        'Report unavailable validation as a limitation, not by itself a blocking finding or a request for evidence. '
        'Return ready when available evidence supports the requested behavior. Return changes_requested only '
        'for concrete defects or failed checks Development can address. If you cannot recommend approval because '
        'of a material unresolved issue or an explicit user requirement you cannot verify, return blocked and '
        'briefly explain why. The user can choose Manual Approve without providing evidence. Do not require '
        'a manual testing checklist or repeatedly return impossible validation tasks to Development.')

    context['project_playbook'] = playbook
    policy = run['library']['skills'].get(POLICY_PATH)
    if policy:
        context['design_system_policy'] = policy
    context['upstream'] = upstream_index(feature, run['stage'])
    context['repository'] = focused_repository(git, feature, run['stage'])
    canonical = {v['artifact'] for v in context['upstream'].values()}
    # Older saved runs and experiments may still contain component catalogs. Keep
    # those out of initial context while preserving access through the frozen library.
    context['shared_library'] = [entry for entry in run['library']['files'] if entry['path'] in canonical or (
        entry['path'].startswith('/skills/') and not entry['path'].startswith((PREFIX + 'components/', PREFIX + 'contracts/')))]
    # Keep room below run_stage's 250k-character input limit. If unusually large
    # artifacts do not fit, retain their immutable pointers rather than silently
    # truncating implementation contracts. Prefer current repair feedback.
    compact = upstream_index(feature)
    detailed = [stage for stage, entry in context['upstream'].items() if entry.get('included_in_context')]
    detailed.sort(key=lambda stage: stage == 'review' and run['stage'] == 'development')
    for stage in detailed:
        if len(json.dumps(context)) <= 220_000:
            break
        context['upstream'][stage] = compact[stage]
