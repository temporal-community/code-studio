from cooperative.context import optimize_context, upstream_index
from cooperative.library import freeze_library, read_library
from cooperative.definitions import PROJECT_FILES
from tests.test_cooperative import setup, request, result


async def test_compact_context_retains_canonical_complete_evidence(setup):
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    prior = {'id': 'prior', 'stage': 'planner', 'status': 'completed', 'input_revision': 1,
             'result': {**result(), 'handoff': 'Complete plan ' * 1000, 'changed_files': ['app.py']}}
    f['pipeline']['runs'].append(prior)
    run = {'id': 'next', 'stage': 'design', 'status': 'running', 'input_revision': 1,
           'result': None, 'context': {}, 'library': freeze_library(f, 'design', {'files': PROJECT_FILES})}
    optimize_context(f, run, p.git, 'Frozen playbook')
    f['pipeline']['runs'].append(run)
    context = run['context']
    entry = context['upstream']['planner']
    assert context['optimization_version'] == 2
    assert entry['included_in_context']
    assert entry['handoff'] == prior['result']['handoff']
    assert entry['deliverables'] == prior['result']['deliverables']
    assert prior['result']['handoff'] in read_library(f, entry['artifact'], 'next')['content']
    assert all('/skills/' in e['path'] or e['path'].endswith('/result.json') for e in context['shared_library'])
    assert context['repository']['excerpts'][0]['path'] == 'app.py'
    assert context['project_playbook'] == 'Frozen playbook'


def completed(stage, ident, revision=2, **changes):
    return {'id': ident, 'stage': stage, 'status': 'completed', 'input_revision': revision,
            'result': {**result(), **changes}}


def test_only_current_predecessor_is_inlined_and_complete_result_is_copied():
    design = completed('design', 'latest', deliverables=[{'title': 'Contract', 'body': 'x' * 6000}])
    feature = {'input_revision': 2, 'pipeline': {'runs': [
        completed('design', 'old-scope', 1), completed('planner', 'plan'),
        completed('design', 'superseded'), design,
        {**completed('design', 'failed'), 'status': 'failed'},
    ]}}
    upstream = upstream_index(feature, 'development')
    assert 'handoff' not in upstream['planner']
    assert upstream['design']['artifact'].endswith('/latest/result.json')
    assert upstream['design']['deliverables'][0]['body'] == 'x' * 6000
    upstream['design']['deliverables'][0]['body'] = 'mutated'
    assert design['result']['deliverables'][0]['body'] == 'x' * 6000


def test_repair_context_keeps_design_and_complete_review_findings_until_superseded():
    finding = {'title': 'Lost edits', 'severity': 'high', 'path': 'app.py', 'line': 3,
               'explanation': 'A stale response replaces current edits.'}
    feature = {'input_revision': 2, 'pipeline': {'runs': [
        completed('design', 'design'), completed('development', 'first'),
        completed('review', 'review', verdict='changes_requested', findings=[finding]),
    ]}}
    upstream = upstream_index(feature, 'development')
    assert upstream['design']['included_in_context']
    assert upstream['review']['included_in_context']
    assert upstream['review']['findings'] == [finding]
    feature['pipeline']['runs'].append(completed('development', 'fixed'))
    assert 'included_in_context' not in upstream_index(feature, 'development')['review']


async def test_legacy_host_runs_also_freeze_query_capabilities_and_library(setup):
    p, root, _, _ = setup
    p.schedule = lambda *_: None
    f = await p.create(request(root))
    run = p.get(f['id'])['pipeline']['runs'][-1]
    assert run['context']['optimization_version'] == 2
    assert run['library']['files']
    hits = await p.callback(f['id'], run['id'], 'search', 'stage_search_source', {'query': 'answer'})
    assert hits['matches'][0]['path'] == 'app.py'
    page = await p.callback(f['id'], run['id'], 'range', 'stage_read_range', {'path': 'app.py', 'line_count': 1})
    assert page['partial']
    skill = await p.callback(f['id'], run['id'], 'shared', 'stage_read_shared_file', {'path': '/skills/ideation/SKILL.md'})
    assert skill['content'] == PROJECT_FILES['ideation_instructions.md']


async def test_oversized_repair_context_uses_complete_artifacts_instead_of_truncation(setup):
    import json
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    large = {
        'deliverables': [{'title': f'Contract {i}', 'body': 'x' * 6000} for i in range(12)],
        'findings': [{'title': f'Issue {i}', 'severity': 'high', 'path': 'app.py', 'line': 1,
                      'explanation': 'y' * 3000} for i in range(30)],
    }
    f['pipeline']['runs'] = [completed('design', 'design', 1, **large),
                            completed('review', 'review', 1, **large)]
    run = {'id': 'repair', 'stage': 'development', 'status': 'running', 'input_revision': 1,
           'result': None, 'context': {}, 'library': freeze_library(f, 'development', {'files': PROJECT_FILES})}
    optimize_context(f, run, p.git, 'Playbook')
    f['pipeline']['runs'].append(run)
    assert len(json.dumps(run['context'])) <= 220_000
    assert 'included_in_context' not in run['context']['upstream']['design']
    assert run['context']['upstream']['review']['deliverables'] == large['deliverables']
    path = run['context']['upstream']['design']['artifact']
    assert json.loads(read_library(f, path, 'repair')['content'])['deliverables'] == large['deliverables']
