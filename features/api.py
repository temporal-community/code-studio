"""Local feature builder API, sharing the workspace's origin guard and broadcasts."""
import asyncio
from pathlib import Path

from fastapi import APIRouter
from pydantic import Field

from .project import validate_code
from .models import StrictModel, FeatureCreate, FeatureEdit, Contribution, FeatureAction


class RepositoryRequest(StrictModel):
    path: str = Field(min_length=1, max_length=1000)


class FileWrite(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    path: str = Field(min_length=1, max_length=300)
    expected_hash: str = Field(max_length=64)
    content: str = Field(max_length=100_000)
    author: str = Field(min_length=1, max_length=40)


def feature_routes(service):
    router = APIRouter(prefix='/workspace/features')

    @router.get('/state')
    async def state():
        s = service()
        return {'features': s.store.list(), 'enabled': s.enabled,
                'demo_repository': str(s.git.demo) if (s.git.demo / '.git').exists() else None}

    @router.get('/agent')
    async def agent_project():
        return service().workspace.feature_project_snapshot()

    @router.post('/agent/validate')
    async def validate_agent():
        project = service().workspace.feature_project_snapshot()
        return {**await validate_code(project['files']), 'version': project['version'], 'revision': project['revision']}

    @router.post('/repository')
    async def repository(request: RepositoryRequest):
        return await asyncio.to_thread(service().git.inspect, request.path)

    @router.post('/demo')
    async def demo():
        s = service()
        async with s.repo_lock:
            return await asyncio.to_thread(s.git.initialize_demo)

    @router.post('')
    async def create(request: FeatureCreate):
        data = request.model_dump()
        if not data['title'].strip() or not data['description'].strip():
            raise ValueError('A title and description are required')
        return await service().create(data)

    @router.get('/{ident}')
    async def get(ident: str):
        return service().store.get(ident)

    @router.post('/{ident}/retry-branch')
    async def retry_branch(ident: str):
        s = service()
        async with s.repo_lock:
            feature = s.store.get(ident)
            if feature['status'] != 'branch_failed':
                return feature
            await asyncio.to_thread(s.git.ensure_worktree, feature)
            feature['status'], feature['error'] = 'draft', None
            s.store.put(feature)
            s.announce(ident)
            return feature

    @router.post('/{ident}/brief')
    async def brief(ident: str, request: FeatureEdit):
        return await service().mutate(ident, 'edit', request.model_dump())

    @router.post('/{ident}/inputs')
    async def contribute(ident: str, request: Contribution):
        return await service().mutate(ident, 'contribute', request.model_dump())

    @router.post('/{ident}/plan')
    async def plan(ident: str, request: FeatureAction):
        return await service().launch(ident, request.model_dump(), 'plan')

    @router.post('/{ident}/plans/{plan_id}/approve')
    async def approve(ident: str, plan_id: str, request: FeatureAction):
        return await service().mutate(ident, 'approve', plan_id, request.model_dump())

    @router.post('/{ident}/code')
    async def code(ident: str, request: FeatureAction):
        return await service().launch(ident, request.model_dump(), 'code')

    @router.post('/{ident}/jobs/{job_id}/cancel')
    async def cancel(ident: str, job_id: str):
        return await service().cancel(ident, job_id)

    @router.get('/{ident}/files')
    async def files(ident: str):
        s = service()
        return {'files': await asyncio.to_thread(s.git.files, s.store.get(ident))}

    @router.get('/{ident}/file')
    async def read(ident: str, path: str):
        s = service()
        feature = s.store.get(ident)
        await asyncio.to_thread(s.git.verify, feature)
        return await asyncio.to_thread(s.git.read, feature, path)

    @router.post('/{ident}/file')
    async def write(ident: str, request: FileWrite):
        s = service()
        async with s.lock(ident):
            return await s.write(ident, None, request.id, request.path, request.expected_hash, request.content, request.author)

    @router.get('/{ident}/diff/files')
    async def diff_files(ident: str):
        s = service()
        return await asyncio.to_thread(s.git.changed_files, s.store.get(ident))

    @router.get('/{ident}/diff/file')
    async def diff_file(ident: str, path: str):
        s = service()
        return await asyncio.to_thread(s.git.file_diff, s.store.get(ident), path)

    @router.get('/{ident}/diff')
    async def diff(ident: str):
        s = service()
        return await asyncio.to_thread(s.git.diff, s.store.get(ident))

    @router.post('/{ident}/syntax')
    async def syntax(ident: str):
        # Parsing is a useful real check without executing repository code.
        import ast
        s = service()
        async with s.lock(ident):
            feature = s.store.get(ident)
            paths = await asyncio.to_thread(s.git.files, feature)
            results = []
            for path in [p for p in paths if Path(p).suffix == '.py'][:100]:
                try:
                    source = await asyncio.to_thread(s.git.read, feature, path)
                    ast.parse(source['content'], filename=path)
                    results.append({'path': path, 'status': 'passed', 'hash': source['hash']})
                except (ValueError, SyntaxError) as exc:
                    results.append({'path': path, 'status': 'failed', 'error': str(exc)})
            result = {'kind': 'python_syntax', 'files': results,
                      'status': 'failed' if any(r['status'] == 'failed' for r in results) else 'passed' if results else 'not_run',
                      'message': 'Python syntax only. Tests and type checks have not been executed.',
                      'truncated': len([p for p in paths if p.endswith('.py')]) > 100}
            feature['syntax_check'] = result
            s.store.put(feature)
            s.announce(ident)
            return result

    return router
