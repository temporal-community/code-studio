"""GitHub evidence capture with deterministic API responses; no credentials or network."""
import base64
import io
import json
import zipfile

import httpx
import pytest

from sdlc.github import GitHub, GitHubError, junit_archive, parse_junit, parse_pr_url

HEAD, BASE, MERGE = 'a' * 40, 'b' * 40, 'c' * 40
JUNIT = b'''<testsuites><testsuite name="unit"><testcase classname="tests.stats" name="positive" time=".2"/>
<testcase classname="tests.stats" name="empty"><failure message="division by zero">stats.py:3</failure></testcase>
<testcase name="optional"><skipped message="missing dataset"/></testcase><testcase name="error"><error>crash</error></testcase></testsuite></testsuites>'''


def archive(content=JUNIT):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as zipped:
        zipped.writestr('test-results/junit.xml', content)
    return output.getvalue()


class API:
    def __init__(self, *, artifacts=True, moving=False, attempt=1, old_artifact=False):
        self.requests = []
        self.artifacts, self.moving, self.attempt, self.old_artifact = artifacts, moving, attempt, old_artifact
        self.pulls = 0
    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == '/repos/acme/demo/pulls/7':
            self.pulls += 1
            return httpx.Response(200, json={'head': {'sha': ('e' * 40 if self.moving and self.pulls > 1 else HEAD), 'repo': {'full_name': 'fork/demo'}},
                'base': {'sha': BASE}, 'merge_commit_sha': MERGE, 'title': 'Fix stats', 'body': 'Details',
                'user': {'login': 'alice'}, 'state': 'open', 'changed_files': 1})
        if path.endswith('/pulls/7/files'):
            return httpx.Response(200, json=[{'filename': 'stats.py', 'status': 'modified', 'additions': 1, 'deletions': 1, 'patch': '@@ -1 +1 @@\n-old\n+new'}])
        if path == '/repos/fork/demo/contents/stats.py':
            assert request.url.params['ref'] == HEAD
            return httpx.Response(200, json={'encoding': 'base64', 'size': 12, 'content': base64.b64encode(b'return 1 / 0').decode()})
        if path.endswith('/check-runs'):
            sha = path.split('/')[-2]
            return httpx.Response(200, json={'check_runs': [{'id': 1 if sha == HEAD else 2, 'name': 'unit', 'status': 'completed', 'conclusion': 'failure', 'head_sha': sha}]})
        if path.endswith('/statuses'):
            return httpx.Response(200, json=[{'id': 20, 'context': 'external CI', 'state': 'pending', 'description': 'Waiting'}])
        if path.endswith('/actions/runs'):
            assert request.url.params['head_sha'] == HEAD
            return httpx.Response(200, json={'workflow_runs': [{'id': 10, 'workflow_id': 1, 'event': 'pull_request', 'run_attempt': self.attempt, 'run_started_at': '2026-10-04T10:00:00Z'}]})
        if path.endswith('/jobs'):
            assert f'/attempts/{self.attempt}/' in path
            return httpx.Response(200, json={'jobs': [{'id': 30, 'name': 'unit', 'conclusion': 'failure', 'steps': [{'name': 'Run pytest', 'conclusion': 'failure'}]}]})
        if path.endswith('/logs'):
            return httpx.Response(200, text='FAILED stats.py\ncredential-secret')
        if path.endswith('/artifacts'):
            return httpx.Response(200, json={'artifacts': [{'id': 40, 'name': 'pytest-test-results', 'size_in_bytes': 400, 'expired': False,
                'created_at': '2026-10-03T10:00:00Z' if self.old_artifact else '2026-10-04T10:01:00Z'}] if self.artifacts else []})
        if path.endswith('/artifacts/40/zip'):
            return httpx.Response(200, content=archive())
        raise AssertionError(f'Unexpected endpoint: {path}')


async def test_capture_pins_fork_contents_checks_attempts_and_parses_test_evidence():
    mock = API(attempt=2)
    github = GitHub('credential-secret', transport=httpx.MockTransport(mock))
    try:
        result = await github.capture('https://github.com/acme/demo/pull/7')
        assert result['pr']['head_sha'] == HEAD and result['pr']['base_sha'] == BASE
        assert {c['sha'] for c in result['checks']} == {HEAD, MERGE}
        assert next(c for c in result['checks'] if c['id'] == 'status-20')['conclusion'] is None
        assert result['tests']['availability'] == 'available'
        assert [c['status'] for c in result['tests']['cases']] == ['passed', 'failed', 'skipped', 'failed']
        assert all(c['attempt'] == 2 for c in result['tests']['cases'])
        assert 'credential-secret' not in json.dumps(result)
        assert '[redacted]' in result['failures'][0]['log']
        assert all(r.headers['authorization'] == 'Bearer credential-secret' for r in mock.requests)
    finally:
        await github.close()


@pytest.mark.parametrize('artifacts,old', [(False, False), (True, True)])
async def test_absent_or_previous_attempt_artifacts_are_not_reported_as_passing(artifacts, old):
    github = GitHub(transport=httpx.MockTransport(API(artifacts=artifacts, attempt=2, old_artifact=old)))
    try:
        result = await github.capture('https://github.com/acme/demo/pull/7')
        assert result['tests'] == {'availability': 'unavailable', 'cases': []}
        assert any('unavailable' in w for w in result['warnings'])
    finally:
        await github.close()


async def test_changed_pr_during_capture_fails_instead_of_mixing_revisions():
    github = GitHub(transport=httpx.MockTransport(API(moving=True)))
    try:
        with pytest.raises(GitHubError, match='PR changed'):
            await github.capture('https://github.com/acme/demo/pull/7')
    finally:
        await github.close()


async def test_pagination_and_explicit_truncation():
    pages = []
    def handler(request):
        page = int(request.url.params['page'])
        pages.append(page)
        return httpx.Response(200, json=list(range((page - 1) * 100, page * 100)))
    github = GitHub(transport=httpx.MockTransport(handler))
    try:
        result, truncated = await github.pages('/example', limit=150)
        assert pages == [1, 2] and len(result) == 150 and truncated
    finally:
        await github.close()


@pytest.mark.parametrize('status,expected', [(401, 'credential'), (404, 'not found'), (403, 'rate limit'), (500, 'HTTP 500')])
async def test_api_errors_are_actionable_and_hide_credentials(status, expected):
    github = GitHub('super-secret', transport=httpx.MockTransport(lambda _: httpx.Response(status, text='super-secret')))
    try:
        with pytest.raises(GitHubError, match=expected) as caught:
            await github.capture('https://github.com/acme/demo/pull/7')
        assert 'super-secret' not in str(caught.value)
    finally:
        await github.close()


@pytest.mark.parametrize('url', ['http://github.com/a/b/pull/1', 'https://github.com.evil/a/b/pull/1', 'https://user@github.com/a/b/pull/1', 'http://127.0.0.1/pull/1', 'https://github.com/a/b/issues/1'])
def test_only_github_pull_request_urls_are_accepted(url):
    with pytest.raises(GitHubError):
        parse_pr_url(url)
    assert parse_pr_url('https://github.com/temporalio/ui/pull/3948') == ('temporalio/ui', 3948)


@pytest.mark.parametrize('xml', [b'<!DOCTYPE foo><testsuite/>', b'<!ENTITY bad "oops"><testsuite/>', '<!DOCTYPE x><testsuite/>'.encode('utf-16'), b'<broken'])
def test_untrusted_xml_is_rejected(xml):
    with pytest.raises(GitHubError):
        parse_junit(xml, artifact='test', run_id=1, attempt=1)


def test_junit_namespaces_and_non_finite_duration():
    cases = parse_junit(b'<testsuite xmlns="test"><testcase name="one" time="inf"/></testsuite>', artifact='junit', run_id=1, attempt=1)
    assert cases[0]['duration'] == 0
    assert cases[0]['status'] == 'passed'


def test_artifact_archive_limits():
    with pytest.raises(GitHubError, match='size limit'):
        junit_archive(archive(b'x' * 5_000_001), artifact='test', run_id=1, attempt=1)
    with pytest.raises(GitHubError, match='ZIP'):
        junit_archive(b'not a zip', artifact='test', run_id=1, attempt=1)


async def test_artifact_redirect_does_not_send_token_to_download_host(monkeypatch):
    import sdlc.github as module
    RealClient = httpx.AsyncClient
    def download(request):
        assert request.url.host == 'example.blob.core.windows.net'
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=archive())
    github = GitHub('secret-token', transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={'location': 'https://example.blob.core.windows.net/signed.zip'})))
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: RealClient(**kw, transport=httpx.MockTransport(download)))
    try:
        assert await github.download('/artifact/zip') == archive()
    finally:
        await github.close()


async def test_artifact_redirect_rejects_unexpected_hosts():
    github = GitHub('secret-token', transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={'location': 'https://evil.example/steal'})))
    try:
        with pytest.raises(GitHubError, match='unsupported'):
            await github.download('/artifact/zip')
    finally:
        await github.close()


async def test_restricted_cli_credential_falls_back_to_public_capture():
    api = API(artifacts=False)
    def transport(request):
        if request.headers.get('authorization'):
            return httpx.Response(403, json={'message': 'Resource not accessible by personal access token'})
        return api(request)
    github = GitHub('restricted-token', transport=httpx.MockTransport(transport), credential_source='GitHub CLI')
    try:
        evidence = await github.capture('https://github.com/acme/demo/pull/7')
        assert evidence['pr']['head_sha'] == HEAD
        assert 'anonymously' in evidence['warnings'][0]
        assert all('authorization' not in request.headers for request in api.requests)
    finally:
        await github.close()
