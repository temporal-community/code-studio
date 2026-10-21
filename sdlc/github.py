"""Read-only GitHub evidence capture. Credentials never enter project files or run artifacts."""
from __future__ import annotations

import base64
import io
import math
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from urllib.parse import quote, urlparse
import xml.etree.ElementTree as ET

import httpx

API = 'https://api.github.com'
MAX_DOWNLOAD = 5_000_000
MAX_TESTS = 1000


class GitHubError(ValueError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def parse_pr_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url.strip())
    match = re.fullmatch(r'/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)(?:/files|/checks|/commits)?/?', parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or not match:
        raise GitHubError('Enter a GitHub PR URL such as https://github.com/owner/repo/pull/123')
    return f'{match[1]}/{match[2]}', int(match[3])


def local_token() -> tuple[str | None, str]:
    for name in ('GITHUB_TOKEN', 'GH_TOKEN'):
        if os.environ.get(name):
            return os.environ[name], name
    if shutil.which('gh'):
        try:
            result = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'],
                                    capture_output=True, text=True, timeout=3, check=False)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), 'GitHub CLI'
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None, 'Public repositories'


def parse_junit(data: bytes, *, artifact: str, run_id, attempt: int) -> list[dict]:
    if len(data) > MAX_DOWNLOAD or b'\x00' in data or re.search(br'<!\s*(DOCTYPE|ENTITY)', data, re.IGNORECASE):
        raise GitHubError('Unsafe or oversized XML test report')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise GitHubError('Invalid XML test report') from exc
    cases = []
    for case in root.iter():
        if case.tag.split('}')[-1] != 'testcase':
            continue
        children = {child.tag.split('}')[-1]: child for child in case}
        failed = children.get('failure')
        if failed is None:
            failed = children.get('error')
        skipped = children.get('skipped')
        detail = failed if failed is not None else skipped
        status = 'failed' if failed is not None else 'skipped' if skipped is not None else 'passed'
        try:
            duration = max(0, float(case.get('time', '0')))
            if not math.isfinite(duration):
                duration = 0
        except ValueError:
            duration = 0
        cases.append({'name': case.get('name', 'Unnamed test')[:300],
                      'suite': case.get('classname', '')[:300], 'status': status,
                      'duration': duration, 'message': ((detail.get('message', '') + '\n' + (detail.text or '')).strip()[:4000] if detail is not None else ''),
                      'artifact': artifact, 'run_id': str(run_id), 'attempt': attempt})
        if len(cases) > MAX_TESTS:
            raise GitHubError('Test report exceeds the 1,000-case capture limit')
    return cases


def junit_archive(data: bytes, *, artifact: str, run_id, attempt: int) -> list[dict]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
        if len(entries) > 100 or sum(e.file_size for e in entries) > MAX_DOWNLOAD:
            raise GitHubError('Test artifact exceeds the archive size limit')
        cases = []
        for entry in entries:
            if entry.filename.lower().endswith('.xml'):
                cases.extend(parse_junit(archive.read(entry), artifact=f'{artifact}/{entry.filename}', run_id=run_id, attempt=attempt))
                if len(cases) > MAX_TESTS:
                    raise GitHubError('Test artifact exceeds the 1,000-case capture limit')
        return cases
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise GitHubError('Could not read the test artifact ZIP') from exc


class GitHub:
    def __init__(self, token: str | None = None, *, transport=None, credential_source='Public repositories'):
        self.token, self.credential_source = token, credential_source
        self.transport = transport
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'code-studio-local'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        self.client = httpx.AsyncClient(base_url=API, headers=headers, timeout=20, transport=transport,
                                        follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    def redact(self, value: str) -> str:
        return value.replace(self.token, '[redacted]') if self.token else value

    async def request(self, path: str, **kwargs):
        try:
            response = await self.client.get(path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError('GitHub could not be reached. Check your network connection.') from exc
        if response.status_code == 401:
            raise GitHubError('GitHub rejected the credential. Set GITHUB_TOKEN or run gh auth login, then restart the workspace host.', 401)
        if response.status_code == 404:
            raise GitHubError('GitHub resource was not found or is not accessible with the current credential.')
        if response.status_code in (403, 429):
            try:
                detail = str(response.json().get('message', ''))[:400]
            except ValueError:
                detail = ''
            raise GitHubError('GitHub denied this request or its rate limit was reached. '
                              'Check repository read permissions and try later. ' + self.redact(detail), response.status_code)
        if response.is_error:
            raise GitHubError(f'GitHub returned HTTP {response.status_code}. Try again later.')
        return response

    async def get(self, path: str, **params):
        response = await self.request(path, params=params)
        return response.json()

    async def pages(self, path: str, key: str | None = None, *, limit=300, **params) -> tuple[list, bool]:
        results = []
        page = 1
        while len(results) < limit:
            payload = await self.get(path, per_page=100, page=page, **params)
            batch = payload[key] if key else payload
            results.extend(batch)
            if len(batch) < 100:
                return results[:limit], len(results) > limit
            page += 1
        return results[:limit], True

    async def download(self, path: str) -> bytes:
        response = await self.request(path)
        if response.is_redirect:
            url = response.headers.get('location', '')
            parsed = urlparse(url)
            host = parsed.hostname or ''
            if parsed.scheme != 'https' or not (host.endswith('.blob.core.windows.net') or host.endswith('.githubusercontent.com') or host.endswith('.github.com')):
                raise GitHubError('GitHub returned an unsupported artifact download host')
            # Signed artifact URLs do not receive the GitHub Authorization header.
            async with httpx.AsyncClient(timeout=25, follow_redirects=False) as downloads:
                async with downloads.stream('GET', url) as stream:
                    if stream.is_error or stream.is_redirect:
                        raise GitHubError('The artifact download has expired or is unavailable')
                    data = bytearray()
                    async for part in stream.aiter_bytes():
                        data.extend(part)
                        if len(data) > MAX_DOWNLOAD:
                            raise GitHubError('Artifact exceeds the 5 MB download limit')
                    return bytes(data)
        if len(response.content) > MAX_DOWNLOAD:
            raise GitHubError('Artifact exceeds the 5 MB download limit')
        return response.content

    async def capture(self, url: str, progress=None) -> dict:
        repo, number = parse_pr_url(url)
        warnings: list[str] = []
        if progress:
            progress('Fetching PR metadata and changed files')
        try:
            pull = await self.get(f'/repos/{repo}/pulls/{number}')
        except GitHubError as exc:
            # A CLI token may be restricted to other organizations. Public PRs should
            # still work anonymously; explicitly configured env credentials fail visibly.
            if self.token and self.credential_source == 'GitHub CLI' and exc.status_code in (401, 403):
                public = GitHub(transport=self.transport)
                try:
                    evidence = await public.capture(url, progress)
                finally:
                    await public.close()
                evidence['warnings'].insert(0, 'The GitHub CLI credential was rejected for this repository. '
                                               'Captured public evidence anonymously; some Actions details may be unavailable.')
                return evidence
            raise
        head, base = pull['head']['sha'], pull['base']['sha']
        head_repo = (pull['head'].get('repo') or {}).get('full_name', repo)
        entries, truncated = await self.pages(f'/repos/{repo}/pulls/{number}/files', limit=100)
        if truncated or pull.get('changed_files', len(entries)) > len(entries):
            warnings.append('Changed-file capture is limited to 100 files. The review does not cover omitted files.')
        files = []
        content_budget = 100_000
        patch_budget = 80_000
        for item in entries:
            patch = item.get('patch', '')
            file = {'path': item['filename'], 'status': item['status'], 'additions': item['additions'],
                    'deletions': item['deletions'], 'patch': patch[:min(12_000, patch_budget)], 'content': None,
                    'content_truncated': False}
            patch_budget -= len(file['patch'])
            if not patch or len(patch) > len(file['patch']):
                warnings.append(f'Diff missing or truncated for {item["filename"]}')
            if len(files) < 20 and content_budget > 0 and item['status'] != 'removed':
                try:
                    payload = await self.get(f'/repos/{head_repo}/contents/{quote(item["filename"], safe="/")}', ref=head)
                    if payload.get('encoding') == 'base64' and payload.get('size', 0) <= 100_000:
                        content = base64.b64decode(payload['content']).decode('utf-8')
                        length = min(20_000, content_budget)
                        file['content'] = content[:length]
                        file['content_truncated'] = len(content) > length
                        content_budget -= len(file['content'])
                    else:
                        warnings.append(f'Content unavailable or too large: {item["filename"]}')
                except (GitHubError, UnicodeError, ValueError):
                    warnings.append(f'Content unavailable: {item["filename"]}')
            files.append(file)
        if len(files) > 20:
            warnings.append('Full contents are captured for at most 20 changed files; other files have diff context only.')
        if progress:
            progress('Collecting CI checks and test artifacts')
        checks = []
        for sha in dict.fromkeys([head, pull.get('merge_commit_sha')]):
            if not sha:
                continue
            try:
                runs, clipped = await self.pages(f'/repos/{repo}/commits/{sha}/check-runs', 'check_runs', limit=200, filter='latest')
                for check in runs:
                    checks.append({'id': str(check['id']), 'name': check['name'], 'status': check['status'],
                                   'conclusion': check.get('conclusion'), 'sha': check['head_sha'],
                                   'url': check.get('html_url'), 'summary': (check.get('output', {}).get('summary') or '')[:3000]})
                if clipped:
                    warnings.append('Check runs truncated at 200 entries per commit.')
                statuses, clipped = await self.pages(f'/repos/{repo}/commits/{sha}/statuses', limit=200)
                contexts = set()
                for status in statuses:
                    if status['context'] in contexts:
                        continue
                    contexts.add(status['context'])
                    checks.append({'id': 'status-' + str(status['id']), 'name': status['context'],
                                   'status': 'in_progress' if status['state'] == 'pending' else 'completed',
                                   'conclusion': {'success': 'success', 'failure': 'failure', 'error': 'failure'}.get(status['state']),
                                   'sha': sha, 'url': status.get('target_url'), 'summary': status.get('description') or ''})
            except GitHubError as exc:
                warnings.append('Some CI checks are unavailable: ' + str(exc))
        checks = list({c['id']: c for c in checks}.values())
        cases, failures = [], []
        try:
            action_runs, clipped = await self.pages(f'/repos/{repo}/actions/runs', 'workflow_runs', limit=100, head_sha=head)
            latest = {}
            for run in action_runs:
                latest.setdefault((run.get('workflow_id'), run.get('event')), run)
            if clipped or len(latest) > 8:
                warnings.append('Actions capture is limited to the latest attempt of eight workflows.')
            for run in list(latest.values())[:8]:
                run_id, attempt = run['id'], run.get('run_attempt', 1)
                try:
                    jobs, _ = await self.pages(f'/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs', limit=100)
                    for job in jobs:
                        if job.get('conclusion') not in ('failure', 'timed_out', 'cancelled'):
                            continue
                        detail = {'job': job['name'], 'run_id': str(run_id), 'attempt': attempt,
                                  'steps': [s['name'] for s in job.get('steps', []) if s.get('conclusion') == 'failure'],
                                  'url': job.get('html_url'), 'log': ''}
                        if len(failures) < 4:
                            try:
                                log = (await self.download(f'/repos/{repo}/actions/jobs/{job["id"]}/logs')).decode('utf-8', errors='replace')
                                detail['log'] = self.redact(log[-12_000:])
                            except GitHubError:
                                detail['log'] = 'Log unavailable; follow the GitHub job link.'
                        failures.append(detail)
                    artifacts, _ = await self.pages(f'/repos/{repo}/actions/runs/{run_id}/artifacts', 'artifacts', limit=100)
                    matching = [a for a in artifacts if re.search(r'junit|pytest|test[-_ ]?(results|report)', a['name'], re.IGNORECASE)]
                    for artifact in matching[:5]:
                        if artifact.get('expired') or artifact.get('size_in_bytes', 0) > MAX_DOWNLOAD:
                            warnings.append(f'Test artifact unavailable or too large: {artifact["name"]}')
                            continue
                        # GitHub artifacts are attached to a run, not reliably to an attempt.
                        # Avoid presenting reports from a previous attempt as current evidence.
                        if attempt > 1 and artifact.get('created_at', '') < run.get('run_started_at', ''):
                            warnings.append(f'Skipped prior-attempt artifact: {artifact["name"]}')
                            continue
                        try:
                            data = await self.download(f'/repos/{repo}/actions/artifacts/{artifact["id"]}/zip')
                            parsed = junit_archive(data, artifact=artifact['name'], run_id=run_id, attempt=attempt)
                            if len(cases) + len(parsed) > MAX_TESTS:
                                warnings.append('Individual test capture limited to 1,000 cases.')
                                break
                            cases.extend(parsed)
                        except GitHubError as exc:
                            warnings.append(f'{artifact["name"]}: {exc}')
                except GitHubError as exc:
                    warnings.append(f'Actions run {run_id}: {exc}')
        except GitHubError as exc:
            warnings.append('Actions evidence unavailable: ' + str(exc))
        # The file-list API addresses a PR, not a SHA. Fail rather than freeze mixed revisions.
        latest_pull = await self.get(f'/repos/{repo}/pulls/{number}')
        if latest_pull['head']['sha'] != head or latest_pull['base']['sha'] != base:
            raise GitHubError('The PR changed while evidence was captured. Start a new run to review its latest revision.')
        if not cases:
            warnings.append('Individual test results are unavailable. CI job status does not establish individual test outcomes.')
        evidence = {'pr': {'url': f'https://github.com/{repo}/pull/{number}', 'repository': repo,
                          'number': number, 'title': pull['title'], 'body': (pull.get('body') or '')[:12_000],
                          'author': pull.get('user', {}).get('login', ''), 'head_sha': head, 'base_sha': base,
                          'head_repository': head_repo, 'state': pull['state']},
                    'files': files, 'checks': checks,
                    'tests': {'availability': 'available' if cases else 'unavailable', 'cases': cases},
                    'failures': failures, 'warnings': warnings, 'captured_at': time.time()}
        # Guard against accidentally retaining an echoed credential in remote diagnostics.
        return json.loads(self.redact(json.dumps(evidence)))
