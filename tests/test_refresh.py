"""Revision probes track public changes and opt in to source I/O."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from cooperative.api import work_routes


@pytest.mark.asyncio
async def test_refresh_revisions_only_read_source_when_requested():
    feature = {'id': 'one', 'updated_at': 1, 'repository': '/repo', 'description': 'Test',
               'pipeline': {'status': 'paused', 'current_stage': 'design', 'stages': {}, 'runs': []}}
    reads = []
    store = SimpleNamespace(list=lambda: [{'id': 'one'}], get=lambda _: deepcopy(feature))
    def fingerprint(value):
        reads.append(value['id'])
        return 'source-' + str(len(reads))
    service = SimpleNamespace(store=store, get=store.get, enabled=True,
                              checks=SimpleNamespace(fingerprint=fingerprint))
    router = work_routes(lambda: service)
    probe = next(r.endpoint for r in router.routes if r.path == '/workspace/platform/revisions')
    before = await probe('one')
    assert before == await probe('one')
    assert not reads
    assert set(await probe()) == {'platform'}
    feature['pipeline']['status'] = 'running'
    after = await probe('one')
    assert before['feature'] != after['feature']
    assert before['platform'] != after['platform']
    files = await probe('one', True)
    changed = await probe('one', True)
    assert files['files'] != changed['files']
    assert files['feature'] == changed['feature']
    assert reads == ['one', 'one']

    def unavailable(_):
        raise ValueError('Workspace unavailable')
    service.checks.fingerprint = unavailable
    failed = await probe('one', True)
    assert failed['feature'] == after['feature']
    assert failed['files_error'] == 'Workspace unavailable'
