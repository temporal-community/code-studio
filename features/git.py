"""Local Git worktrees and bounded file access. No shell commands from model output."""
from __future__ import annotations

import hashlib
import difflib
import os
from pathlib import Path, PurePosixPath
import subprocess
import shutil
import tempfile

MAX_FILE = 100_000
BLOCKED_PARTS = {'.git', '.local', '.venv', 'node_modules', '__pycache__', '.aws', '.ssh', '.codex'}
BLOCKED_NAMES = {'credentials', 'id_rsa', 'id_ed25519'}


def file_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or not path.parts or len(name) > 300 or '\\' in name or '\x00' in name or path.is_absolute() or '..' in path.parts:
        raise ValueError('Use a relative path inside the feature worktree')
    if any(p in BLOCKED_PARTS or p.startswith('.env') or p in BLOCKED_NAMES for p in path.parts):
        raise ValueError('Repository metadata and credential files are not available to agents')
    if path.suffix.lower() in ('.pem', '.key', '.p12', '.pfx'):
        raise ValueError('Credential files are not available to agents')
    return path


class LocalGit:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.worktrees = self.directory / 'feature-worktrees'
        self.demo = self.directory / 'feature-demo'

    def git(self, repository: str | Path, *args: str, timeout=20) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_TERMINAL_PROMPT='0', LC_ALL='C')
        try:
            result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                     '-C', str(repository), *args], capture_output=True, text=True,
                                    timeout=timeout, check=False, env=env)
        except FileNotFoundError as exc:
            raise ValueError('Install Git to create feature branches') from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError('The local Git operation timed out; retry after checking the repository') from exc
        if result.returncode:
            raise ValueError((result.stderr.strip() or 'Git operation failed')[:1500])
        return result.stdout.rstrip('\n')

    def inspect(self, repository: str) -> dict:
        path = Path(repository).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('Choose an existing local repository, or create the demo project')
        try:
            root = self.git(path, 'rev-parse', '--show-toplevel')
            head = self.git(path, 'rev-parse', '--verify', 'HEAD^{commit}')
        except ValueError as exc:
            raise ValueError('This directory needs a Git repository with an initial commit. Choose another repository or create the demo project.') from exc
        return {'path': root, 'head': head, 'branches': self.git(root, 'for-each-ref', '--format=%(refname:short)', 'refs/heads').splitlines(),
                'current_branch': self.git(root, 'branch', '--show-current'),
                'dirty': bool(self.git(root, 'status', '--porcelain')), 'demo': Path(root) == self.demo}

    def initialize_demo(self) -> dict:
        if (self.demo / '.git').exists():
            return self.inspect(str(self.demo))
        if self.demo.exists() and any(self.demo.iterdir()):
            raise ValueError('The demo directory already contains files. Choose an existing repository instead.')
        self.directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='feature-demo-init-', dir=self.directory))
        try:
            (staging / 'app.py').write_text('''"""Small task-list project for collaborative feature demos."""


def add_task(tasks: list[dict], title: str) -> dict:
    task = {"id": len(tasks) + 1, "title": title, "done": False}
    tasks.append(task)
    return task


def list_tasks(tasks: list[dict]) -> list[dict]:
    return list(tasks)
''')
            (staging / 'test_app.py').write_text('''import unittest
from app import add_task, list_tasks


class TasksTest(unittest.TestCase):
    def test_add_task(self):
        tasks = []
        task = add_task(tasks, "Write a plan")
        self.assertEqual(task["title"], "Write a plan")
        self.assertFalse(task["done"])
        self.assertEqual(list_tasks(tasks), [task])


if __name__ == "__main__":
    unittest.main()
''')
            (staging / 'README.md').write_text('# Shared task list\n\nA tiny Python task list. Run checks with `python -m unittest discover -v`.\n\nTry adding task completion or filtering. Preserve existing behavior.\n')
            self.git(staging, 'init', '-b', 'main')
            self.git(staging, 'add', 'app.py', 'test_app.py', 'README.md')
            self.git(staging, '-c', 'user.name=Code Studio Demo', '-c', 'user.email=demo@localhost', '-c', 'commit.gpgsign=false', 'commit', '-m', 'Initialize feature demo')
            staging.rename(self.demo)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self.inspect(str(self.demo))

    def prepare(self, ident: str, repository: str, branch: str, base: str) -> dict:
        info = self.inspect(repository)
        if branch.startswith('-') or branch == 'HEAD':
            raise ValueError('Choose a valid new branch name, such as feature/task-filter')
        self.git(info['path'], 'check-ref-format', '--branch', branch)
        sha = self.git(info['path'], 'rev-parse', '--verify', '--end-of-options', base + '^{commit}')
        if branch in info['branches']:
            raise ValueError('That branch already exists. Choose a new feature branch name.')
        return {'repository': info['path'], 'branch': branch, 'base': base, 'base_sha': sha,
                'worktree': str(self.worktrees / ident), 'source_dirty': info['dirty']}

    def ensure_worktree(self, feature: dict):
        target = Path(feature['worktree'])
        self.worktrees.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.verify(feature)
            return
        self.git(feature['repository'], 'worktree', 'add', '-b', feature['branch'], str(target), feature['base_sha'], timeout=45)
        self.verify(feature)

    def verify(self, feature: dict):
        target = Path(feature['worktree'])
        if target.resolve().parent != self.worktrees or target.is_symlink():
            raise ValueError('Feature worktree is outside its managed directory')
        if not target.exists() or Path(self.git(target, 'rev-parse', '--show-toplevel')).resolve() != target.resolve():
            raise ValueError('Feature worktree is unavailable')
        if self.git(target, 'branch', '--show-current') != feature['branch']:
            raise ValueError('The feature worktree branch changed. Restore its recorded branch before continuing.')
        origin = Path(self.git(feature['repository'], 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        current = Path(self.git(target, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if origin != current:
            raise ValueError('The feature worktree no longer belongs to the selected repository')

    def resolve_file(self, feature: dict, name: str) -> Path:
        relative = safe_path(name)
        root = Path(feature['worktree'])
        path = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError('Symbolic links are not available to agents')
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('File is outside the feature worktree')
        return path

    def files(self, feature: dict) -> list[str]:
        self.verify(feature)
        paths = self.git(feature['worktree'], 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\x00')
        visible = []
        for name in sorted(set(paths)):
            if not name:
                continue
            try:
                self.resolve_file(feature, name)
            except ValueError:
                continue
            visible.append(name)
        return visible[:3000]

    def read(self, feature: dict, name: str) -> dict:
        path = self.resolve_file(feature, name)
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError('Choose an existing text file smaller than 100 KB')
        try:
            with path.open(encoding='utf-8', newline='') as handle:
                content = handle.read()
        except UnicodeError as exc:
            raise ValueError('Only UTF-8 text files are supported') from exc
        if '\x00' in content:
            raise ValueError('Binary files are not supported')
        return {'path': name, 'content': content, 'hash': file_hash(content)}

    def write(self, feature: dict, name: str, expected_hash: str, content: str) -> dict:
        self.verify(feature)
        path = self.resolve_file(feature, name)
        if len(content.encode()) > MAX_FILE or '\x00' in content:
            raise ValueError('File must contain UTF-8 text smaller than 100 KB')
        before = self.read(feature, name)['hash'] if path.exists() else ''
        if before != expected_hash:
            raise ValueError('File changed since it was read. Reread and preserve the collaborator’s changes.')
        path.parent.mkdir(parents=True, exist_ok=True)
        # Recheck parents after mkdir to reject existing symlink paths.
        path = self.resolve_file(feature, name)
        fd, temporary = tempfile.mkstemp(prefix='.feature-write-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
                handle.write(content)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {'path': name, 'hash': file_hash(content), 'saved': True}

    def context(self, feature: dict) -> dict:
        paths = self.files(feature)
        selected = sorted(paths, key=lambda p: (Path(p).name.lower() not in {'readme.md', 'pyproject.toml', 'package.json', 'app.py', 'test_app.py'}, p))
        excerpts, budget = [], 40_000
        for path in selected[:12]:
            try:
                file = self.read(feature, path)
            except ValueError:
                continue
            text = file['content'][:min(6000, budget)]
            excerpts.append({**file, 'content': text, 'truncated': len(text) < len(file['content'])})
            budget -= len(text)
            if not budget:
                break
        return {'base_sha': feature['base_sha'], 'head_sha': self.git(feature['worktree'], 'rev-parse', 'HEAD'),
                'paths': paths[:500], 'file_count': len(paths), 'excerpts': excerpts,
                'note': 'Bounded local repository context. Unshown files have not been inspected.'}

    def changed_files(self, feature: dict) -> dict:
        """Return safe paths changed from the base, including worktree additions/deletions."""
        self.verify(feature)
        root = feature['worktree']
        tracked = self.git(root, 'diff', '--name-only', '--no-renames', '-z',
                           '--no-ext-diff', '--no-textconv', feature['base_sha'], '--').split('\x00')
        tracked = [name for name in tracked if name]
        untracked = self.git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\x00')
        untracked = [name for name in untracked if name]
        tracked_limited, untracked_limited = tracked[:400], untracked[:40]
        added = set(self.git(root, 'diff', '--diff-filter=A', '--name-only', '--no-renames', '-z',
                             '--no-ext-diff', '--no-textconv', feature['base_sha'], '--').split('\x00'))
        deleted = set(self.git(root, 'diff', '--diff-filter=D', '--name-only', '--no-renames', '-z',
                               '--no-ext-diff', '--no-textconv', feature['base_sha'], '--').split('\x00'))
        entries = []
        for name in tracked_limited:
            try:
                path = self.resolve_file(feature, name)
            except ValueError:
                continue
            change = 'deleted' if name in deleted or not path.exists() else 'added' if name in added else 'modified'
            readable = False
            if change != 'deleted':
                try:
                    self.read(feature, name)
                    readable = True
                except (ValueError, OSError):
                    pass
            # Text diffs for unreadable/binary/oversized worktree files are not offered.
            diff_available = change == 'deleted' or readable
            entries.append({'path': name, 'change': change, 'content_available': readable,
                            'diff_available': diff_available})
        for name in untracked_limited:
            try:
                self.resolve_file(feature, name)
            except ValueError:
                continue
            readable = False
            try:
                self.read(feature, name)
                readable = True
            except (ValueError, OSError):
                pass
            entries.append({'path': name, 'change': 'added', 'content_available': readable,
                            'diff_available': readable})
        return {'base_sha': feature['base_sha'], 'files': sorted(entries, key=lambda item: item['path']),
                'truncated': len(tracked) > 400 or len(untracked) > 40}

    def file_diff(self, feature: dict, name: str) -> dict:
        """Return a bounded diff for one safe changed path only."""
        safe_path(name)
        listing = self.changed_files(feature)
        entry = next((item for item in listing['files'] if item['path'] == name), None)
        if entry is None:
            raise ValueError('File is not changed from the feature base revision')
        patch = ''
        if entry['diff_available']:
            if entry['change'] == 'added' and name in self.git(feature['worktree'], 'ls-files', '--others', '--exclude-standard', '-z').split('\x00'):
                file = self.read(feature, name)
                patch = ''.join(difflib.unified_diff([], file['content'].splitlines(keepends=True),
                                                     fromfile='/dev/null', tofile='b/' + name))
            else:
                patch = self.git(feature['worktree'], 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                                 feature['base_sha'], '--', ':(literal)' + name)
        return {'path': name, 'patch': patch[:120_000], 'truncated': len(patch) > 120_000,
                'content_available': entry['content_available'], 'diff_available': entry['diff_available']}

    def diff(self, feature: dict) -> dict:
        """Legacy combined-diff response retained for existing callers."""
        self.verify(feature)
        changed = self.git(feature['worktree'], 'diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv', feature['base_sha']).split('\x00')
        visible = []
        for name in changed:
            if not name:
                continue
            try:
                self.resolve_file(feature, name)
            except ValueError:
                continue
            visible.append(name)
        selected = visible[:400]
        tracked = self.git(feature['worktree'], 'diff', '--no-ext-diff', '--no-textconv', feature['base_sha'], '--',
                           *[':(literal)' + name for name in selected]) if selected else ''
        untracked = self.git(feature['worktree'], 'ls-files', '--others', '--exclude-standard').splitlines()
        added = []
        for name in untracked[:40]:
            try:
                file = self.read(feature, name)
            except ValueError:
                continue
            added.append(file)
        return {'patch': tracked[:120_000], 'truncated': len(tracked) > 120_000 or len(visible) > 400 or len(untracked) > 40,
                'new_files': added, 'worktree': feature['worktree'], 'branch': feature['branch']}
