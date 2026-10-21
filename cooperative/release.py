"""Local release packages and explicit, conflict-checked application to a source directory."""
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

from features.git import safe_path
from .source import bytes_hash


def preview(git, feature, run_id):
    git.verify(feature)
    directory = git.directory / 'releases' / feature['id']
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (run_id + '.zip')
    paths = git.files(feature)
    manifest = {'feature': feature['title'], 'branch': feature['branch'], 'created_at': time.time(),
                'source_directory': feature['repository'], 'base': feature['base_sha'], 'kind': 'local_release_preview',
                'files': {}, 'checks': feature['pipeline'].get('checks', {}), 'remote_deployment': False}
    total = 0
    with zipfile.ZipFile(target.with_suffix('.tmp'), 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in paths:
            path = git.resolve_file(feature, name)
            if not path.is_file(): continue
            data = path.read_bytes(); total += len(data)
            if total > 50_000_000: raise ValueError('Release preview exceeds 50 MB')
            archive.writestr('source/' + name, data)
            manifest['files'][name] = bytes_hash(data)
        archive.writestr('release-manifest.json', json.dumps(manifest, indent=2))
    os.replace(target.with_suffix('.tmp'), target)
    return {'path': str(target), 'file_count': len(manifest['files']), 'bytes': total,
            'kind': 'local_release_preview', 'source_hash': bytes_hash(json.dumps(manifest['files'], sort_keys=True).encode()),
            'download': '/workspace/work/' + feature['id'] + '/release', 'created_at': manifest['created_at']}


def apply_to_source(git, feature):
    if feature.get('workspace_mode') != 'snapshot':
        raise ValueError('This run uses a Git worktree. Review and merge its branch with Git.')
    changes = git.changes(feature)
    root = Path(feature['source_directory']).resolve()
    prepared = []
    for change in changes:
        relative = safe_path(change['path'])
        target = root.joinpath(*relative.parts)
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError('Unsafe source path: ' + change['path'])
        for parent in target.parents:
            if parent == root: break
            if parent.is_symlink(): raise ValueError('Symlink source directory: ' + change['path'])
        current = bytes_hash(target.read_bytes()) if target.is_file() else ''
        if current == change['after_hash']:
            continue  # A previously completed or partially recovered application.
        if current != change['before_hash']:
            raise ValueError('Source changed since this feature started: ' + change['path'] + '. Resolve it before applying; no files were changed.')
        prepared.append((change, target))
    for change, target in prepared:
        # Recheck immediately before each replacement, including after mkdir.
        current = bytes_hash(target.read_bytes()) if target.is_file() else ''
        if current != change['before_hash']:
            raise ValueError('Source changed during application: ' + change['path'] + '. Earlier file applications are retained; retry after resolving the conflict.')
        if change['kind'] == 'deleted':
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = git.resolve_file(feature, change['path']).read_bytes()
            fd, temporary = tempfile.mkstemp(prefix='.studio-apply-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as handle: handle.write(data)
                if target.exists(): os.chmod(temporary, target.stat().st_mode & 0o777)
                os.replace(temporary, target)
            finally: Path(temporary).unlink(missing_ok=True)
    return {'applied': [c['path'] for c, _ in prepared], 'source_directory': str(root), 'time': time.time()}
