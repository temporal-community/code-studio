"""Git worktrees when available; isolated source snapshots for ordinary local directories."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from features.git import LocalGit, safe_path
from sdlc.models import digest

EXCLUDED = {'.git', '.local', '.venv', 'node_modules', 'dist', 'build', '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.svelte-kit', '.idea', '.vscode', '.DS_Store'}
MARKER = '.code-studio-workspace.json'


def bytes_hash(data):
    return hashlib.sha256(data).hexdigest()


def source_paths(root):
    paths = []
    total = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in EXCLUDED and not d.startswith('.env') and not Path(current, d).is_symlink()]
        for name in sorted(names):
            path = Path(current, name)
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or name == MARKER or name in EXCLUDED:
                continue
            try:
                safe_path(relative)
            except ValueError:
                continue
            size = path.stat().st_size
            if size > 2_000_000:
                raise ValueError('A source file exceeds the 2 MB snapshot limit: ' + relative)
            total += size
            if total > 50_000_000 or len(paths) >= 10_000:
                raise ValueError('This codebase exceeds the 50 MB / 10,000-file local snapshot limit')
            paths.append(relative)
    return sorted(paths)


class SourceWorkspace(LocalGit):
    def prepare(self, ident, repository, branch, base, *, allow_snapshot=False, prefer_snapshot=False):
        root = Path(repository).expanduser().resolve()
        if not root.is_dir():
            raise ValueError('The working directory does not exist')
        if not prefer_snapshot:
            try:
                return super().prepare(ident, repository, branch, base)
            except ValueError as exc:
                if not allow_snapshot:
                    raise
                try:
                    self.inspect(str(root))
                except ValueError:
                    pass
                else:
                    raise exc
        if not branch.strip() or len(branch) > 160 or branch.startswith('-'):
            raise ValueError('Choose a name for the isolated feature workspace')
        manifest = {p: bytes_hash((root / p).read_bytes()) for p in source_paths(root)}
        if not manifest:
            raise ValueError('The working directory has no supported source files')
        return {'repository': str(root), 'source_directory': str(root), 'branch': branch, 'base': 'current files',
                'base_sha': digest(manifest), 'source_manifest': manifest,
                'worktree': str(self.worktrees / ident), 'baseline': str(self.directory / 'feature-baselines' / ident),
                'source_dirty': False, 'workspace_mode': 'snapshot'}

    def ensure_worktree(self, feature):
        if feature.get('workspace_mode') != 'snapshot':
            return super().ensure_worktree(feature)
        target = Path(feature['worktree'])
        baseline = Path(feature['baseline'])
        if target.exists():
            self.verify(feature)
            return
        baseline.parent.mkdir(parents=True, exist_ok=True)
        self.worktrees.mkdir(parents=True, exist_ok=True)
        if not baseline.exists():
            staging = Path(tempfile.mkdtemp(prefix='snapshot-', dir=baseline.parent))
            try:
                root = Path(feature['source_directory'])
                for name, expected in feature['source_manifest'].items():
                    src = root / name
                    if src.is_symlink() or any(p.is_symlink() for p in src.parents if p != root.parent):
                        raise ValueError('Source changed to a symlink during snapshot creation')
                    data = src.read_bytes()
                    if bytes_hash(data) != expected:
                        raise ValueError('Source changed during workspace creation. Create a new feature to capture it.')
                    dest = staging / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    os.chmod(dest, src.stat().st_mode & 0o777)
                staging.rename(baseline)
            finally:
                if staging.exists(): shutil.rmtree(staging)
        staging = Path(tempfile.mkdtemp(prefix='feature-', dir=self.worktrees))
        try:
            shutil.copytree(baseline, staging, dirs_exist_ok=True)
            (staging / MARKER).write_text(json.dumps({'feature_id': feature['id'], 'base_sha': feature['base_sha']}))
            staging.rename(target)
        finally:
            if staging.exists(): shutil.rmtree(staging)
        self.verify(feature)

    def verify(self, feature):
        if feature.get('workspace_mode') != 'snapshot':
            return super().verify(feature)
        target = Path(feature['worktree'])
        if target.is_symlink() or target.resolve().parent != self.worktrees:
            raise ValueError('Feature workspace is outside its managed directory')
        try:
            marker = json.loads((target / MARKER).read_text())
        except (OSError, ValueError) as exc:
            raise ValueError('The isolated feature workspace is unavailable') from exc
        if marker != {'feature_id': feature['id'], 'base_sha': feature['base_sha']}:
            raise ValueError('The feature workspace identity changed')

    def resolve_file(self, feature, name):
        if name == MARKER:
            raise ValueError('Workspace metadata cannot be edited')
        return super().resolve_file(feature, name)

    def files(self, feature):
        if feature.get('workspace_mode') != 'snapshot':
            return super().files(feature)
        self.verify(feature)
        return source_paths(Path(feature['worktree']))

    def context(self, feature):
        if feature.get('workspace_mode') != 'snapshot':
            return super().context(feature)
        paths = self.files(feature)
        priorities = {'readme.md', 'pyproject.toml', 'package.json', 'app.py', 'test_app.py', 'agents.toml'}
        selected = sorted(paths, key=lambda p: (Path(p).name.lower() not in priorities, p))
        excerpts, budget = [], 40_000
        for path in selected[:14]:
            try: file = self.read(feature, path)
            except ValueError: continue
            text = file['content'][:min(5000, budget)]
            excerpts.append({**file, 'content': text, 'truncated': len(text) < len(file['content'])})
            budget -= len(text)
            if not budget: break
        return {'base_sha': feature['base_sha'], 'paths': paths[:500], 'file_count': len(paths), 'excerpts': excerpts,
                'note': 'Isolated snapshot of current source files. Use read tools for unshown files. Source checkout remains unchanged.'}

    def changes(self, feature):
        self.verify(feature)
        if feature.get('workspace_mode') == 'snapshot':
            original = feature['source_manifest']
        else:
            names = self.git(feature['worktree'], 'ls-tree', '-r', '--name-only', feature['base_sha']).splitlines()
            original = {}
            for name in names:
                try:
                    safe_path(name)
                    original[name] = None
                except ValueError: pass
        current = self.files(feature)
        changes = []
        for name in sorted(set(original) | set(current)):
            target = self.resolve_file(feature, name)
            data = target.read_bytes() if target.is_file() else None
            if feature.get('workspace_mode') == 'snapshot':
                try: old = (Path(feature['baseline']) / name).read_bytes() if name in original else None
                except OSError as exc: raise ValueError('Saved baseline is unavailable: ' + name + '. Restore the baseline before reviewing or integrating') from exc
                if old is not None and bytes_hash(old) != original[name]:
                    raise ValueError('Saved baseline changed: ' + name)
            else:
                try: old = self.git(feature['worktree'], 'show', feature['base_sha'] + ':' + name).encode() if name in original else None
                except ValueError: old = None
                # Git text helper strips the final newline; only used for the visible preview here.
                if old is not None and data is not None and old == data.rstrip(b'\n'): continue
            old_mode = (Path(feature['baseline']) / name).stat().st_mode & 0o777 if feature.get('workspace_mode') == 'snapshot' and old is not None else None
            new_mode = target.stat().st_mode & 0o777 if data is not None else None
            if old != data or (feature.get('workspace_mode') == 'snapshot' and old_mode != new_mode):
                changes.append({'path': name, 'before_hash': bytes_hash(old) if old is not None else '',
                                'after_hash': bytes_hash(data) if data is not None else '', 'before_mode': old_mode, 'after_mode': new_mode,
                                'kind': 'deleted' if data is None else 'added' if old is None else 'modified'})
        return changes

    def changed_files(self, feature):
        if feature.get('workspace_mode') != 'snapshot':
            return super().changed_files(feature)
        changes = self.changes(feature)
        entries = []
        for change in changes[:400]:
            name = change['path']; readable = change['kind'] != 'deleted'
            if readable:
                try: self.read(feature, name)
                except (ValueError, OSError): readable = False
            entries.append({'path': name, 'change': change['kind'], 'content_available': readable,
                            'diff_available': readable or change['kind'] == 'deleted'})
        return {'base_sha': feature['base_sha'], 'files': entries, 'truncated': len(changes) > 400}

    def file_diff(self, feature, name):
        if feature.get('workspace_mode') != 'snapshot':
            return super().file_diff(feature, name)
        safe_path(name)
        entry = next((item for item in self.changed_files(feature)['files'] if item['path'] == name), None)
        if entry is None: raise ValueError('File is not changed from the saved baseline')
        before = Path(feature['baseline']) / name
        after = self.resolve_file(feature, name)
        patch = ''; available = entry['diff_available']
        if available:
            try:
                a = before.read_text() if before.exists() else ''
                b = after.read_text() if after.exists() else ''
                if '\0' in a or '\0' in b: raise UnicodeError('Binary file')
                patch = ''.join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                    fromfile='a/' + name if before.exists() else '/dev/null',
                    tofile='b/' + name if after.exists() else '/dev/null'))
            except UnicodeError: available = False
        return {'path': name, 'patch': patch[:120_000], 'truncated': len(patch) > 120_000,
                'content_available': entry['content_available'], 'diff_available': available}

    def diff(self, feature):
        if feature.get('workspace_mode') != 'snapshot':
            return super().diff(feature)
        changes = self.changes(feature)
        patch = ''
        for change in changes[:100]:
            name = change['path']
            before = Path(feature['baseline']) / name
            after = self.resolve_file(feature, name)
            try:
                a = before.read_text() if before.exists() else ''
                b = after.read_text() if after.exists() else ''
            except UnicodeError:
                patch += 'Binary file changed: ' + name + '\n'
                continue
            patch += ''.join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True), fromfile='a/' + name, tofile='b/' + name))
            if len(patch) > 120_000: break
        return {'patch': patch[:120_000], 'truncated': len(patch) > 120_000 or len(changes) > 100,
                'changes': changes, 'new_files': [], 'worktree': feature['worktree'], 'branch': feature['branch']}
