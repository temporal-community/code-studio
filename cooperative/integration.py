"""Immutable integration candidates and journaled, reversible local application."""
import asyncio
import difflib
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
import uuid

from features.git import safe_path
from sdlc.models import digest
from .checks import CHECKS, TIMEOUTS
from .source import bytes_hash, source_paths


def safe_target(root, name):
    path = root.joinpath(*safe_path(name).parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Unsafe integration path: ' + name)
    for parent in path.parents:
        if parent == root: break
        if parent.is_symlink(): raise ValueError('Symlink directory: ' + name)
    if path.exists() and not path.is_file(): raise ValueError('Expected a file: ' + name)
    return path


def file_state(path):
    return {'hash': bytes_hash(path.read_bytes()), 'mode': path.stat().st_mode & 0o777} if path.is_file() else {'hash': '', 'mode': None}


def manifest(root):
    return {name: file_state(safe_target(root, name)) for name in source_paths(root)}


def durable_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.journal-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output); output.flush(); os.fsync(output.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally: Path(temp).unlink(missing_ok=True)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def sync_parents(path, stop):
    while True:
        sync_directory(path)
        if path == stop or path.parent == path: break
        path = path.parent


def replace_file(target, data, mode, root=None):
    if data is None:
        target.unlink(missing_ok=True); sync_parents(target.parent, root or target.parent); return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.studio-apply-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data); output.flush(); os.fchmod(output.fileno(), mode); os.fsync(output.fileno())
        os.replace(temp, target); sync_parents(target.parent, root or target.parent)
    finally: Path(temp).unlink(missing_ok=True)


def merge_text(local, baseline, proposed):
    """A clean three-way merge preserves local edits; conflicts never get applied."""
    for path in (local, baseline, proposed):
        data = path.read_bytes()
        if b'\0' in data: return None
        try: data.decode('utf-8')
        except UnicodeError: return None
    # Git can report overlap for a large original rewrite even when the local
    # version contains every proposed line unchanged plus later additions.
    local_bytes = local.read_bytes()
    proposed_lines = proposed.read_bytes().splitlines(keepends=True)
    local_lines = local_bytes.splitlines(keepends=True)
    if proposed_lines and all(tag in ('equal', 'insert') for tag, *_ in
                             difflib.SequenceMatcher(None, proposed_lines, local_lines, autojunk=False).get_opcodes()):
        return local_bytes
    if not shutil.which('git'): return None
    result = subprocess.run(['git', 'merge-file', '-p', '--diff3', '--', str(local), str(baseline), str(proposed)],
                            capture_output=True, timeout=30)
    return result.stdout if result.returncode == 0 else None


class Integrations:
    def __init__(self, git, checks):
        self.git, self.checks = git, checks
        self.directory = git.directory / 'integrations'

    def path(self, ident):
        if not ident or any(c not in '0123456789abcdef' for c in ident) or len(ident) != 32:
            raise ValueError('Invalid integration ID')
        return self.directory / ident / 'journal.json'

    def load(self, feature):
        ident = feature['pipeline'].get('integration_id')
        if not ident: return None
        record = json.loads(self.path(ident).read_text())
        if record['feature_id'] != feature['id']: raise ValueError('Integration belongs to another feature')
        return record

    def save(self, record):
        durable_json(self.path(record['id']), record)

    def require_release(self, feature):
        pipeline = feature['pipeline']
        if feature.get('experiment_id'): raise ValueError('Adopt experiment changes before integrating')
        if pipeline['status'] != 'complete' or not pipeline.get('release'):
            raise ValueError('Complete the release preview first')
        if self.checks.fingerprint(feature) != pipeline['release']['workspace_hash']:
            raise ValueError('Feature files changed after release. Rerun Review and Deployment')
        # Diff failure must never be interpreted as an empty change list.
        self.git.changed_files(feature)

    def prepare(self, feature, author):
        self.require_release(feature)
        previous = self.load(feature)
        if previous and previous['status'] in ('applying', 'recovery_required', 'applied'):
            raise ValueError('Roll back or retain the existing application before creating another preview')
        if feature.get('workspace_mode') != 'snapshot':
            raise ValueError('Git worktree features must be reviewed and merged on their branch; automatic file copying is disabled')
        root = Path(feature['source_directory']).resolve()
        before = manifest(root)
        changes = self.git.changes(feature)
        conflicts = []; already_present = []; auto_merged = []
        ident = uuid.uuid4().hex
        candidate = {'id': ident, **self.git.prepare(ident, str(root), 'integration-' + ident, 'HEAD', prefer_snapshot=True)}
        self.git.ensure_worktree(candidate)
        # Give validation its own Git boundary. Git commands in a nested copy
        # must never discover and operate on the user's parent checkout.
        if shutil.which('git'):
            command = ['git', '-C', candidate['worktree'], '-c', 'core.hooksPath=/dev/null',
                       '-c', 'commit.gpgsign=false', '-c', 'user.name=Code Studio',
                       '-c', 'user.email=integration@code-studio.invalid']
            for args in (['-c', 'init.templateDir=', 'init', '-b', 'studio-integration'],
                         ['add', '--all'], ['commit', '--allow-empty', '-m', 'Local integration baseline']):
                subprocess.run(command + args, check=True, capture_output=True, timeout=30,
                               env=self.checks.environment(candidate))
        for change in changes:
            name = change['path']; local = file_state(safe_target(root, name))
            target = self.git.resolve_file(candidate, name)
            if local == {'hash': change['after_hash'], 'mode': change['after_mode']}:
                already_present.append(name)
                continue
            if local not in ({'hash': change['before_hash'], 'mode': change['before_mode']},
                             {'hash': change['after_hash'], 'mode': change['after_mode']}):
                merged = None
                if change['kind'] == 'modified' and local['hash'] and local['mode'] in (change['before_mode'], change['after_mode']):
                    merged = merge_text(target, Path(feature['baseline']) / name, self.git.resolve_file(feature, name))
                if merged is not None:
                    target.write_bytes(merged); os.chmod(target, change['after_mode'])
                    if file_state(target) == local: already_present.append(name)
                    else: auto_merged.append(name)
                    continue
                conflicts.append({'path': name, 'reason': 'Overlapping edits, incompatible permissions, or a binary/add/delete conflict needs manual reconciliation'})
                continue
            if change['kind'] == 'deleted': target.unlink(missing_ok=True)
            else:
                source = self.git.resolve_file(feature, name); data = source.read_bytes()
                if bytes_hash(data) != change['after_hash']: raise ValueError('Feature changed while preparing integration')
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
                os.chmod(target, source.stat().st_mode & 0o777)
        if before != manifest(root): raise ValueError('Local files changed while preparing integration. Try again')
        self.require_release(feature)
        after = manifest(Path(candidate['worktree']))
        delta = [{'path': name, 'before': before.get(name, {'hash': '', 'mode': None}),
                  'after': after.get(name, {'hash': '', 'mode': None}),
                  'kind': 'added' if name not in before else 'deleted' if name not in after else 'modified'}
                 for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
        record = {'id': ident, 'feature_id': feature['id'], 'revision': feature['input_revision'],
                  'author': author, 'created_at': time.time(), 'status': 'conflicts' if conflicts else 'preview',
                  'source_directory': str(root), 'release_hash': feature['pipeline']['release']['workspace_hash'],
                  'candidate': candidate, 'before': before, 'after': after, 'changes': delta,
                  'diff': self.git.diff(candidate),
                  'conflicts': conflicts, 'checks': {}, 'written': [], 'manual_overrides': feature['pipeline'].get('manual_approvals', [])}
        record.update(already_present=already_present, auto_merged=auto_merged, proposed_file_count=len(changes))
        self.save(record)
        return record

    def assert_current(self, feature, record):
        self.require_release(feature)
        if record['revision'] != feature['input_revision'] or record['release_hash'] != feature['pipeline']['release']['workspace_hash']:
            raise ValueError('Release changed. Prepare a new integration preview')
        if manifest(Path(record['source_directory'])) != record['before']:
            raise ValueError('Local files changed. Prepare and validate a new integration preview')
        if manifest(Path(record['candidate']['worktree'])) != record['after']:
            raise ValueError('Integration candidate changed. Prepare a new preview')

    async def validate(self, feature, record):
        if record['status'] not in ('preview', 'failed', 'verified', 'validating'):
            raise ValueError('Resolve conflicts and prepare a new preview before validating')
        await asyncio.to_thread(self.assert_current, feature, record)
        previous = [*record.get('checks', {}).values(),
                    *feature['pipeline'].get('checks', {}).values(),
                    *feature['pipeline'].get('check_cache', {}).values()]
        record.update(status='validating', validation_started_at=time.time(), error='',
                      checks={name: {'check': name, 'status': 'queued'} for name in CHECKS})
        self.save(record)

        async def check(name):
            candidate = record['candidate']
            before = await asyncio.to_thread(self.checks.identity, candidate, name)
            cached = next((value for value in previous if value.get('check') == name
                and value.get('cache_key') == before['cache_key']
                and value.get('scope', 'full') == 'full'
                and value.get('status') in ('passed', 'not_available')), None)
            if cached:
                record['checks'][name] = {**cached, 'cached': True}; self.save(record)
                return
            record['checks'][name] = {'check': name, 'status': 'running', 'started_at': time.time(),
                                      'timeout_seconds': TIMEOUTS.get(name, 120)}
            self.save(record)
            result = await self.checks.run(candidate, name)
            after = await asyncio.to_thread(self.checks.identity, candidate, name)
            if before != after or result['source_hash'] != before['source_hash']:
                result = {**result, 'status': 'stale'}
            record['checks'][name] = {**result, **before, 'cached': False}; self.save(record)

        async def frontend_checks():
            # npm scripts share generated files, so keep them ordered. Python
            # tests can run alongside this lane; verify all inputs at the barrier.
            for name in ('browser_tests', 'types', 'build'):
                await check(name)

        tasks = []
        try:
            await check('syntax')
            if record['checks']['syntax']['status'] in ('passed', 'not_available'):
                tasks = [asyncio.create_task(check('python_tests')), asyncio.create_task(frontend_checks())]
                await asyncio.gather(*tasks)
            await asyncio.to_thread(self.assert_current, feature, record)
            for name, evidence in record['checks'].items():
                if evidence.get('cache_key') and evidence['cache_key'] != (await asyncio.to_thread(self.checks.identity, record['candidate'], name))['cache_key']:
                    evidence['status'] = 'stale'
            record['status'] = 'verified' if all(c['status'] in ('passed', 'not_available') for c in record['checks'].values()) else 'failed'
            record['validated_at'] = time.time()
        except BaseException as exc:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            record.update(status='failed', error=str(exc) or 'Checks interrupted. Retry the merge.')
            raise
        finally:
            for evidence in record['checks'].values():
                if evidence['status'] in ('running', 'queued'):
                    evidence['status'] = 'interrupted' if record.get('error') else 'not_run'
            self.save(record)
        return record

    def conflict(self, feature, record, path):
        self.assert_current(feature, record)
        if path not in {item['path'] for item in record['conflicts']}:
            raise ValueError('This file no longer has an unresolved conflict')
        def version(root):
            file = safe_target(Path(root).resolve(), path)
            state = file_state(file)
            data = file.read_bytes() if file.is_file() else b''
            try: content = data.decode('utf-8') if len(data) <= 100_000 and b'\0' not in data else None
            except UnicodeError: content = None
            return {**state, 'exists': file.is_file(), 'content': content}
        return {'path': path, 'integration_id': record['id'],
                'local': version(record['candidate']['baseline']),
                'feature': version(feature['worktree']), 'base': version(feature['baseline'])}

    def resolve(self, feature, record, request):
        if request['integration_id'] != record['id']:
            raise ValueError('The merge changed. Reload the conflict before resolving it.')
        if any(item['id'] == request['id'] for item in record.get('resolutions', [])):
            return record
        if record['status'] != 'conflicts': raise ValueError('There are no conflicts to resolve')
        path = request['path']
        versions = self.conflict(feature, record, path)
        target = safe_target(Path(record['candidate']['worktree']).resolve(), path)
        choice = request['choice']
        if choice == 'feature':
            source = safe_target(Path(feature['worktree']).resolve(), path)
            data = source.read_bytes() if source.is_file() else None
            mode = versions['feature']['mode']
        elif choice == 'local':
            source = safe_target(Path(record['candidate']['baseline']).resolve(), path)
            data = source.read_bytes() if source.is_file() else None
            mode = versions['local']['mode']
        elif choice == 'edit':
            if any(versions[side]['content'] is None for side in ('local', 'feature')):
                raise ValueError('Use one version for binary or large-file conflicts')
            content = request.get('content')
            if content is None: raise ValueError('Enter the resolved file content')
            if any(line.startswith(('<<<<<<<', '=======', '>>>>>>>', '|||||||')) for line in content.splitlines()):
                raise ValueError('Remove the merge-conflict markers before saving')
            data = content.encode('utf-8')
            mode = versions['local']['mode'] or versions['feature']['mode'] or 0o644
        else: raise ValueError('Choose local, feature, or edit')
        if data is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data); os.chmod(target, mode)
        record['conflicts'] = [item for item in record['conflicts'] if item['path'] != path]
        record['after'] = manifest(Path(record['candidate']['worktree']))
        before, after = record['before'], record['after']
        record['changes'] = [{'path': name, 'before': before.get(name, {'hash': '', 'mode': None}),
            'after': after.get(name, {'hash': '', 'mode': None}),
            'kind': 'added' if name not in before else 'deleted' if name not in after else 'modified'}
            for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
        record['diff'] = self.git.diff(record['candidate'])
        record.setdefault('resolutions', []).append({'id': request['id'], 'path': path, 'choice': choice,
                                                   'author': request['author'], 'time': time.time()})
        record.update(status='conflicts' if record['conflicts'] else 'preview', checks={}, error='')
        self.save(record)
        return record

    @contextmanager
    def source_lock(self, record):
        path = self.directory / ('source-' + digest(record['source_directory']) + '.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(lock, fcntl.LOCK_UN)

    def apply(self, feature, record, author):
        with self.source_lock(record):
            if record['status'] == 'applied': return record
            if record['status'] != 'verified': raise ValueError('Prepare and validate integration before applying')
            self.assert_current(feature, record)
            for name, check in record['checks'].items():
                if check['cache_key'] != self.checks.identity(record['candidate'], name)['cache_key']:
                    raise ValueError('Validation environment changed. Validate integration again')
            # Verify and freeze bytes before touching any local file.
            root = Path(record['source_directory']); candidate = Path(record['candidate']['worktree'])
            payloads = {}
            for change in record['changes']:
                name = change['path']; path = safe_target(candidate, name)
                if file_state(path) != change['after']: raise ValueError('Candidate changed: ' + name)
                backup = safe_target(Path(record['candidate']['baseline']), name)
                if file_state(backup) != change['before']: raise ValueError('Backup changed: ' + name)
                if backup.is_file():
                    with backup.open('rb') as saved: os.fsync(saved.fileno())
                    sync_parents(backup.parent, Path(record['candidate']['baseline']).parent)
                payloads[name] = path.read_bytes() if path.is_file() else None
                if (bytes_hash(payloads[name]) if payloads[name] is not None else '') != change['after']['hash']:
                    raise ValueError('Candidate changed while reading: ' + name)
            record.update(status='applying', apply_author=author, written=[], error=''); self.save(record)
            try:
                for change in record['changes']:
                    name = change['path']; target = safe_target(root, name)
                    if file_state(target) != change['before']: raise ValueError('Local file changed during application: ' + name)
                    replace_file(target, payloads[name], change['after']['mode'], root)
                    record['written'].append(name); self.save(record)
                if manifest(root) != record['after']: raise ValueError('Local files changed during application')
                record.update(status='applied', applied_at=time.time())
                self.save(record)
            except Exception as exc:
                record.update(status='recovery_required', error=str(exc)); self.save(record)
                self._rollback(record, author)
                raise ValueError('Application stopped: ' + str(exc) + '. Recovery state: ' + record['status']) from exc
        return record

    def rollback(self, record, author):
        with self.source_lock(record): return self._rollback(record, author)

    def _rollback(self, record, author):
        if record['status'] == 'rolled_back': return record
        if record['status'] not in ('applied', 'applying', 'recovery_required'):
            raise ValueError('No application to roll back')
        root = Path(record['source_directory']); restore = []; conflicts = []
        for change in record['changes']:
            target = safe_target(root, change['path']); current = file_state(target)
            if current == change['before']: continue
            if current != change['after']:
                conflicts.append(change['path']); continue
            backup = safe_target(Path(record['candidate']['baseline']), change['path'])
            if file_state(backup) != change['before']: raise ValueError('Backup changed: ' + change['path'])
            data = backup.read_bytes() if backup.is_file() else None
            if (bytes_hash(data) if data is not None else '') != change['before']['hash']:
                raise ValueError('Backup changed while reading: ' + change['path'])
            restore.append((change, data))
        if conflicts:
            record.update(status='recovery_required', rollback_conflicts=conflicts); self.save(record)
            return record
        record['status'] = 'recovery_required'; self.save(record)
        for change, data in reversed(restore):
            target = safe_target(root, change['path'])
            if file_state(target) != change['after']:
                record['rollback_conflicts'] = [change['path']]; self.save(record); return record
            replace_file(target, data, change['before']['mode'], root)
        if any(file_state(safe_target(root, c['path'])) != c['before'] for c in record['changes']):
            raise ValueError('Rollback interrupted by local edits; retry recovery')
        record.update(status='rolled_back', rolled_back_at=time.time(), rollback_author=author, rollback_conflicts=[])
        self.save(record); return record

    def public(self, record):
        if not record: return None
        return {k: v for k, v in record.items() if k not in ('candidate', 'before', 'after')}

    def inspect_commit(self, record):
        root = Path(record['source_directory'])
        if not shutil.which('git'): return {'status': 'unavailable', 'detail': 'Git is not installed'}
        def git(*args):
            return subprocess.run(['git', '--literal-pathspecs', '-C', str(root), *args], capture_output=True, timeout=10)
        top = git('rev-parse', '--show-toplevel')
        if top.returncode or Path(top.stdout.decode().strip()).resolve() != root:
            return {'status': 'unavailable', 'detail': 'Commit verification requires a Git repository root'}
        head = git('rev-parse', 'HEAD')
        if head.returncode: return {'status': 'not_committed'}
        sha = head.stdout.decode().strip()
        for change in record['changes']:
            name = change['path']; expected = change['after']
            if file_state(safe_target(root, name)) != expected:
                return {'status': 'local_changes', 'head': sha}
            tree = git('ls-tree', sha, '--', name)
            if tree.returncode: return {'status': 'unavailable'}
            if expected['hash'] == '':
                if tree.stdout: return {'status': 'not_committed', 'head': sha}
            else:
                data = git('show', sha + ':' + name)
                mode = b'100755' if expected['mode'] & 0o111 else b'100644'
                if data.returncode or bytes_hash(data.stdout) != expected['hash'] or not tree.stdout.startswith(mode + b' '):
                    return {'status': 'not_committed', 'head': sha}
        return {'status': 'committed' if record['changes'] else 'no_changes', 'head': sha}
