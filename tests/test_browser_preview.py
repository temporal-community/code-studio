"""Real browser navigation and deny-by-default access to a shared backend."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from cooperative.preview_gateway import gateway
from cooperative.browser_preview import relative_path
from agent_dag.browser_tools import browser_enabled, model_tool, stage_browser
from agent_dag.stage_agent import stage_model_callbacks
from tests.test_source_patches import active, setup as setup
from tests.test_stage_code_mode import settings


@pytest.fixture(autouse=True)
async def close_previews(request):
    pipeline = request.getfixturevalue('setup')[0] if 'setup' in request.fixturenames else None
    yield
    if pipeline: await pipeline.browser_previews.stop()


HTML = '''<!doctype html><html><head><title>Preview fixture</title></head><body>
<h1>Feature preview</h1><a href="/next">Next page</a><a href="http://example.com">External</a>
<details><summary>Feature details</summary><p>Expanded content</p></details>
<form action="/workspace/work/one" method="post"><input name="x"><button>Save changes</button></form>
<button id="write">Attempt write</button><script src="/app.js"></script>
</body></html>'''
SCRIPT = '''document.querySelector('#write').onclick=()=>fetch('/workspace/work/one',{method:'POST',body:'change'});
fetch('/workspace/platform').then(r=>r.json()).then(v=>{document.querySelector('h1').textContent=v.title});'''


def backend():
    app = FastAPI(); calls = []
    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def read(path: str, request: Request):
        calls.append((request.method, '/' + path))
        if path == 'workspace/health': return RedirectResponse('/workspace/unsafe')
        return {'title': 'Live backend data'}
    return app, calls


@pytest.fixture
def site(tmp_path):
    root = tmp_path / 'site'; root.mkdir()
    (root / 'index.html').write_text(HTML); (root / 'app.js').write_text(SCRIPT)
    return root


def test_gateway_blocks_writes_unknown_reads_redirects_and_socket_mutations(site):
    app, calls = backend()
    with TestClient(gateway(site, app, lambda: {'revision': 7})) as client:
        assert client.get('/workspace/platform').json()['title'] == 'Live backend data'
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            assert client.request(method, '/workspace/platform').status_code == 403
        for path in ('/workspace/work/one/control/resume', '/workspace/unknown', '/api/write', '/harness/api',
                     '/workspace/platform%2f..%2funsafe', '/workspace/platform%252fanything'):
            assert client.get(path).status_code == 403
        assert client.get('/workspace/health').status_code == 502
        assert calls == [('GET', '/workspace/platform'), ('GET', '/workspace/health')]
        html = client.get('/next')
        assert '/__preview_guard.js' in html.text
        assert "form-action 'none'" in html.headers['Content-Security-Policy']
        with client.websocket_connect('/workspace/connect', headers={'origin': 'http://testserver'}) as ws:
            assert ws.receive_json()['revision'] == 7
            for message in ({'type':'edit'}, {'type':'prompt'}, {'type':'presence'}):
                ws.send_json(message); assert ws.receive_json()['type'] == 'error'
            ws.send_json({'type':'ping'}); assert ws.receive_json()['type'] == 'pong'
        assert len(calls) == 2


@pytest.mark.parametrize('path', ['https://localhost/', '//localhost/', '/\\localhost', '/workspace/work/x', '/api/x', '/../x'])
def test_browser_rejects_non_page_paths(path):
    with pytest.raises(ValueError): relative_path(path)


async def configured(setup, monkeypatch):
    p, feature, run = await active(setup)
    root = Path(feature['worktree'])
    (root / 'ui').mkdir(); (root / 'ui/index.html').write_text(HTML)
    (root / 'package.json').write_text(json.dumps({'name':'code-studio','scripts':{'build':'vite build'}}))
    (root / '.gitignore').write_text('dist/\n')
    async def build(*args):
        output = root / 'dist'; output.mkdir(exist_ok=True)
        (output / 'index.html').write_text(HTML); (output / 'app.js').write_text(SCRIPT)
        return {'status':'passed'}
    monkeypatch.setattr(p.checks, 'run', AsyncMock(side_effect=build))
    app, calls = backend(); p.browser_previews.backend = app
    run['context']['browser_preview_version'] = 1; p.store.put(feature)
    return p, feature, run, calls


async def test_real_browser_readonly_navigation_receipts_staleness_and_cleanup(setup, monkeypatch):
    p, feature, run, calls = await configured(setup, monkeypatch)
    service = p.browser_previews
    args = {'action':'open','path':'/','target':'','width':375,'height':812}
    first = await p.callback(feature['id'], run['id'], 'open', 'stage_browser', args)
    assert first['image'].startswith('data:image/jpeg;base64,') and 'Live backend data' in first['snapshot']
    assert 'Read-only preview' in first['snapshot']
    instance = service.instances[feature['id']]; page = instance['sessions'][run['id']]['page']
    assert await page.get_by_role('button', name='Save changes').is_disabled()
    await page.get_by_role('button', name='Save changes').evaluate('(button) => button.disabled = false')
    await page.wait_for_timeout(50)
    assert await page.get_by_role('button', name='Save changes').is_disabled()
    assert await page.locator('a', has_text='External').get_attribute('href') is None
    # The agent browser blocks writes before they reach the gateway.
    with pytest.raises(Exception):
        await page.evaluate("fetch('/workspace/work/one',{method:'POST',body:'x'}).then(r=>r.status)")
    # Even without browser request interception (a human tab), the gateway rejects writes.
    await session_unroute(instance, run['id'])
    assert await page.evaluate("fetch('/workspace/work/one',{method:'POST',body:'x'}).then(r=>r.status)") == 403
    with pytest.raises(Exception):
        await page.evaluate("fetch('http://127.0.0.1:8002/workspace/platform').then(r=>r.status)")
    before = list(calls)
    assert await p.callback(feature['id'], run['id'], 'open', 'stage_browser', args) == first
    assert calls == before
    next_args = {**args, 'action':'navigate','target':'Feature details'}
    observed = await p.callback(feature['id'], run['id'], 'details', 'stage_browser', next_args)
    assert 'Expanded content' in observed['snapshot']
    again = await p.callback(feature['id'], run['id'], 'details', 'stage_browser', next_args)
    assert again == observed and await page.locator('details').get_attribute('open') is not None
    with pytest.raises(ValueError):
        await p.callback(feature['id'], run['id'], 'bad', 'stage_browser', {**next_args,'target':'Attempt write'})
    with pytest.raises(ValueError, match='interrupted'):
        await p.callback(feature['id'], run['id'], 'bad', 'stage_browser', {**next_args,'target':'Attempt write'})
    assert all(method == 'GET' for method, _ in calls)
    assert p.checks.run.await_count == 1
    changed_scope = p.get(feature['id']); changed_scope['input_revision'] += 1; p.store.put(changed_scope)
    assert all(row['stale'] for row in (await service.status(feature['id']))['evidence'])
    (Path(feature['worktree']) / 'app.py').write_text('newer = True\n')
    status = await service.status(feature['id'])
    assert status['stale'] and all(row['stale'] for row in status['evidence'])
    image = service.image_path(feature['id'], first['id']); assert image.exists()
    await service.close(feature['id'])
    assert image.exists() and not instance['directory'].exists() and instance['task'].done()
    assert (await service.status(feature['id']))['url'] is None
    assert (await service.observe(feature['id'], run['id'], args, first['id']))['image'] == first['image']


async def session_unroute(instance, owner):
    await instance['sessions'][owner]['context'].unroute('**/*')


async def test_browser_permissions_and_model_image_output(setup, monkeypatch):
    p, feature, run, _ = await configured(setup, monkeypatch)
    args = {'action':'open','path':'/','target':'','width':1000,'height':800}
    for tools, context in [(['stage_read_file'], {'browser_preview_version':1}),
                           (settings('development').tools, {})]:
        config = settings('development').model_copy(update={'tools':tools})
        assert not browser_enabled(config, context)
        assert stage_browser not in stage_model_callbacks(config, context)
    run['context'].pop('browser_preview_version'); p.store.put(feature)
    with pytest.raises(ValueError, match='unavailable'):
        await p.callback(feature['id'], run['id'], 'denied', 'stage_browser', args)
    runner = type('Runner', (), {'run_tool': AsyncMock(return_value={'snapshot':'Page', 'image':'data:image/jpeg;base64,YQ=='})})()
    tool = model_tool(runner, stage_browser)
    result = await tool.on_invoke_tool(None, json.dumps(args))
    assert result[0].type == 'text' and result[1].type == 'image'
    assert result[1].image_url.startswith('data:image/')


async def test_failed_and_changed_builds_do_not_launch_preview(setup, monkeypatch):
    p, feature, _, _ = await configured(setup, monkeypatch)
    p.checks.run.side_effect = None; p.checks.run.return_value = {'status':'failed','output':'broken import'}
    with pytest.raises(ValueError, match='broken import'): await p.browser_previews.start(feature['id'])
    assert not p.browser_previews.instances
    async def changing(*args):
        (Path(feature['worktree']) / 'app.py').write_text('changed = True')
        return {'status':'passed'}
    p.checks.run.side_effect = changing
    with pytest.raises(ValueError, match='Source changed'): await p.browser_previews.start(feature['id'])
    assert not p.browser_previews.instances


# Exercise the browser callback through a real durable stage, without a model service.
from tests.test_pipeline_harness import durable as durable, waiting, decision
from tests.test_cooperative import request as feature_request


async def test_real_temporal_stages_capture_browser_evidence(durable, monkeypatch):
    p, root, _, _ = durable
    (root / 'ui').mkdir(); (root / 'ui/index.html').write_text(HTML)
    (root / 'package.json').write_text(json.dumps({'name':'code-studio','scripts':{'build':'vite build'}}))
    (root / '.gitignore').write_text('dist/\n')
    original = p.checks.run
    async def build(feature, check, targets=()):
        if check != 'build': return await original(feature, check, targets)
        output = Path(feature['worktree']) / 'dist'; output.mkdir(exist_ok=True)
        (output / 'index.html').write_text(HTML); (output / 'app.js').write_text(SCRIPT)
        return {'check':'build', 'status':'passed', 'source_hash':p.checks.fingerprint(feature),
                'started_at':0, 'finished_at':0, 'output':'Fixture built', 'exit_code':0, 'command':['fixture']}
    monkeypatch.setattr(p.checks, 'run', build)
    p.browser_previews.backend, calls = backend()
    feature = await p.create(feature_request(root))
    approval = await waiting(p, feature['id'], 'planner')
    await p.approve_managed(feature['id'], decision(approval))
    await waiting(p, feature['id'], 'review')
    runs = p.get(feature['id'])['pipeline']['runs']
    for stage in ('development','review'):
        run = next(r for r in runs if r['stage'] == stage)
        assert any(t['name'] == 'stage_browser' for t in run['trace'])
    evidence = (await p.browser_previews.status(feature['id']))['evidence']
    assert len(evidence) == 2 and not any(e['stale'] for e in evidence)
    assert all(method == 'GET' for method, _ in calls)
