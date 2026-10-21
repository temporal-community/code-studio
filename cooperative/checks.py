"""Explicit local checks. No model-provided commands, installs, or inherited API credentials."""
import asyncio
import ast
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import site

from sdlc.models import digest

CHECKS = ('syntax', 'python_tests', 'browser_tests', 'types', 'build')
TIMEOUTS = {'python_tests': 300}


class CheckRunner:
    def __init__(self, git):
        self.git = git

    def environment(self, feature):
        source = Path(feature['repository'])
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE='1', CI='1', CODE_STUDIO_PYTHON=sys.executable)
        env['PATH'] = str(source / 'node_modules/.bin') + os.pathsep + env.get('PATH', '')
        return env

    def targets(self, feature, check, targets):
        if not isinstance(targets, (list, tuple)) or len(targets) > 30 or any(not isinstance(p, str) for p in targets):
            raise ValueError('Choose up to 30 source test paths')
        targets = sorted(set(targets))
        if not targets: return []
        if check not in ('syntax', 'python_tests'):
            raise ValueError('This check requires full-project coverage')
        allowed = set(self.git.files(feature))
        for path in targets:
            if path not in allowed or path.startswith('-') or not path.endswith('.py'):
                raise ValueError('Choose an existing permitted Python file')
            if check == 'python_tests' and not (Path(path).name.startswith('test_') or Path(path).name.endswith('_test.py')):
                raise ValueError('Focused Python checks require test file paths')
        if check == 'python_tests' and not (Path(feature['worktree']) / 'pyproject.toml').exists():
            raise ValueError('Focused tests require a configured pytest project')
        return targets

    def identity(self, feature, check, targets=()):
        """Dependency metadata is rescanned, including edits inside installed packages.

        File metadata avoids rereading every installed dependency's bytes. Explicit
        force reruns remain available for external state and nondeterministic tests.
        """
        if check not in CHECKS:
            raise ValueError('Unknown check')
        self.git.verify(feature)
        targets = self.targets(feature, check, targets)
        available = self.available(feature)[check]
        try:
            command = self.command(feature, check, targets) if check != 'syntax' and available else []
        except ValueError:
            command = []
        roots = ({Path(feature['repository']) / '.venv', *[Path(p) for p in site.getsitepackages()]}
                 if check == 'python_tests' else
                 {Path(feature['repository']) / 'node_modules', Path(feature['worktree']) / 'node_modules'}
                 if check != 'syntax' else set())
        dependencies = []
        for root in sorted({p.resolve() for p in roots}, key=str):
            if not root.exists():
                dependencies.append((str(root), None)); continue
            for directory, dirs, names in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', '.cache', '.vite', '.vite-temp', '.pytest_cache'))
                for name in sorted(names):
                    if name.endswith(('.pyc', '.pyo')): continue
                    path = Path(directory, name)
                    try: stat = path.stat()
                    except FileNotFoundError: stat = path.lstat()
                    dependencies.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        for binary in {sys.executable, shutil.which('node'), shutil.which('npm'), command[0] if command else None} - {None}:
            path = Path(binary).resolve(); stat = path.stat()
            dependencies.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        source_hash = self.fingerprint(feature)
        return {'source_hash': source_hash, 'cache_key': digest({'version': 2, 'source': source_hash,
            'check': check, 'targets': targets, 'command': command, 'available': available,
            'timeout_seconds': TIMEOUTS.get(check, 120),
            'environment': self.environment(feature), 'dependencies': dependencies})}

    def available(self, feature):
        root = Path(feature['worktree'])
        source = Path(feature['repository'])
        npm = (root / 'package.json')
        package = json.loads(npm.read_text()) if npm.exists() else {}
        scripts = package.get('scripts', {})
        return {'syntax': True, 'python_tests': (root / 'pyproject.toml').exists() or any(root.glob('test*.py')) or (root / 'tests').exists(),
                'browser_tests': bool(scripts.get('test:collaboration')), 'types': bool(scripts.get('check')),
                'build': bool(scripts.get('build')), 'dependency_directory': str(source)}

    def command(self, feature, check, targets=()):
        root, source = Path(feature['worktree']), Path(feature['repository'])
        python = str(source / '.venv/bin/python') if (source / '.venv/bin/python').exists() else sys.executable
        if check == 'python_tests':
            if (root / 'pyproject.toml').exists():
                command = [python, '-m', 'pytest', '-v', '--durations=10', '-o', 'faulthandler_timeout=60']
                if targets: return command + list(targets)
                if (root / 'tests/test_check_code.py').exists() and (root / 'tests/test_execute_flow.py').exists():
                    command += ['--ignore=tests/test_check_code.py', '--ignore=tests/test_execute_flow.py']
                return command
            return [python, '-m', 'unittest', 'discover', '-v']
        npm = shutil.which('npm')
        if not npm:
            raise ValueError('npm is not installed')
        return [npm, 'run', {'browser_tests': 'test:collaboration', 'types': 'check', 'build': 'build'}[check]]

    async def run(self, feature, check, targets=()):
        if check not in CHECKS:
            raise ValueError('Unknown check; choose ' + ', '.join(CHECKS))
        await asyncio.to_thread(self.git.verify, feature)
        targets = await asyncio.to_thread(self.targets, feature, check, targets)
        paths = await asyncio.to_thread(self.git.files, feature)
        fingerprint = await asyncio.to_thread(self.fingerprint, feature, paths)
        result = {'check': check, 'started_at': time.time(), 'source_hash': fingerprint,
                  'scope': 'focused' if targets else 'full', 'targets': targets,
                  'status': 'not_available', 'exit_code': None, 'output': '', 'command': []}
        available = self.available(feature)
        if not available[check]:
            return {**result, 'output': 'No configured ' + check.replace('_', ' ') + ' command was found.'}
        if check == 'syntax':
            failures, count = [], 0
            for name in targets or paths:
                if not name.endswith('.py'): continue
                count += 1
                try:
                    file = await asyncio.to_thread(self.git.read, feature, name)
                    ast.parse(file['content'], filename=name)
                except (ValueError, SyntaxError) as exc: failures.append(str(exc))
            return {**result, 'status': 'failed' if failures else 'passed', 'exit_code': 1 if failures else 0,
                    'output': '\n'.join(failures) if failures else f'Parsed {count} Python files successfully.', 'finished_at': time.time()}
        try: command = self.command(feature, check, targets)
        except ValueError as exc: return {**result, 'output': str(exc)}
        source = Path(feature['repository'])
        env = self.environment(feature)
        result['command'] = command
        timeout = TIMEOUTS.get(check, 120)
        result['timeout_seconds'] = timeout
        process = await asyncio.create_subprocess_exec(*command, cwd=feature['worktree'], env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        output = bytearray(); truncated = False
        async def consume():
            nonlocal truncated
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 48_000:
                    del output[:-48_000]; truncated = True
            await process.wait()
        try:
            await asyncio.wait_for(consume(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError): raise
            return {**result, 'status': 'timed_out', 'output': output.decode(errors='replace') + f'\nCheck exceeded {timeout} seconds.', 'truncated': truncated, 'finished_at': time.time()}
        text = output.decode(errors='replace')
        return {**result, 'status': 'passed' if process.returncode == 0 else 'failed', 'exit_code': process.returncode,
                'output': text, 'truncated': truncated, 'finished_at': time.time()}

    def fingerprint(self, feature, paths=None):
        from .source import bytes_hash
        root = Path(feature['worktree'])
        return digest({p: bytes_hash((root / p).read_bytes()) for p in (paths or self.git.files(feature)) if (root / p).is_file()})
