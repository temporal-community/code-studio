"""Loopback shared workspace service. Run: python -m workspace.server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import fcntl
import json
import logging
import os
import sqlite3
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal
from urllib.parse import urlparse

from features.api import feature_routes
from features.runtime import FeatureService
from features.storage import FeatureConflict

from cooperative.api import work_routes
from cooperative.runtime import PipelineService
from cooperative.definitions import DEFINITIONS
from sdlc.catalog import catalog
from sdlc.github import parse_pr_url
from sdlc.runtime import RunService, validate_code
from sdlc.storage import RunStore, evidence_tool
from sdlc.templates import TOOL_CATALOG

from .store import WorkspaceStore
from .formatting import format_python

ROOT = Path(__file__).resolve().parents[1]
MAX_MESSAGE_BYTES = 10_000_000  # Character IDs make a 20k-character paste several MB.
# Clients ping every 15–20 seconds. Allow background timer throttling, but do not
# keep cached pages or abandoned proxy connections in presence indefinitely.
PRESENCE_IDLE_TIMEOUT = 120


class Edit(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9:_-]+$')
    operation: dict
    author: str = Field(min_length=1, max_length=40)
    file: str = 'app.py'


class Prompt(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9:_-]+$')
    text: str = Field(min_length=1, max_length=8000)
    author: str = Field(min_length=1, max_length=40)


class FormatRequest(BaseModel):
    source: str = Field(max_length=20_000)


class ReviewSource(BaseModel):
    kind: Literal['github', 'fixture']
    url: str = Field(default='', max_length=500)
    fixture: str = Field(default='', max_length=120)


class ReviewRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_-]+$')
    source: ReviewSource
    author: str = Field(min_length=1, max_length=40)
    revision: int = Field(ge=0)


class CommentRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    author: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=4000)
    finding: int | None = None


class FixtureRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PreviewRequest(BaseModel):
    fixture: str
    tool: str
    arguments: dict = Field(default_factory=dict)


def create_app(directory: Path | None = None, *, with_agent: bool = True,
               review_runtime=None, github=None, feature_runtime=None, pipeline_runtime=None) -> FastAPI:
    directory = directory or Path(os.environ.get('WORKSPACE_DIR', ROOT / '.local/shared-project'))
    peers: dict[str, dict] = {}
    store: WorkspaceStore
    host = None
    feature_host = None
    studio_hosts = {}
    studio_tasks = []
    pipeline: PipelineService
    reviews: RunStore
    service: RunService
    features: FeatureService

    def publish(message):
        for peer in list(peers.values()):
            try:
                peer['queue'].put_nowait(message)
            except asyncio.QueueFull:
                # Disconnect a slow tab; it will reconnect with a complete snapshot.
                asyncio.create_task(peer['socket'].close(code=1013))

    def presence():
        return {'type': 'presence', 'peers': [
            {'id': ident, 'name': p['name'], 'cursor': p['cursor'], 'file': p['file']}
            for ident, p in peers.items()
        ]}

    @asynccontextmanager
    async def lifespan(app):
        nonlocal store, host, feature_host, reviews, service, features, pipeline
        directory.mkdir(parents=True, exist_ok=True)
        lock = (directory / 'host.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('This workspace already has a callback host running')
        store = WorkspaceStore(directory)
        app.state.store = store
        reviews = RunStore(store)
        service = RunService(reviews, publish, runtime=review_runtime, github=github,
                             enabled=with_agent or review_runtime is not None)
        app.state.reviews, app.state.review_service = reviews, service
        await service.start()
        features = FeatureService(store, directory, publish, runtime=feature_runtime,
                                  enabled=with_agent or feature_runtime is not None)
        app.state.features = features
        await features.start()
        pipeline = PipelineService(features, publish, runtime=pipeline_runtime)
        pipeline.browser_previews.backend = app
        pipeline.browser_previews.snapshot = store.snapshot
        app.state.pipeline = pipeline
        await pipeline.start()
        task = None
        feature_task = None
        if with_agent:
            from .agent_host import AgentHost
            host = AgentHost(store, publish)
            task = asyncio.create_task(host.run())
            feature_host = AgentHost(store, publish, project='feature')
            feature_task = asyncio.create_task(feature_host.run())
            for agent_id in DEFINITIONS:
                studio_hosts[agent_id] = AgentHost(store, publish, project='agent:' + agent_id)
                studio_tasks.append(asyncio.create_task(studio_hosts[agent_id].run()))
        try:
            yield
        finally:
            await pipeline.stop()
            await features.stop()
            await service.stop()
            for job in list(prompt_jobs):
                job.cancel()
            await asyncio.gather(*prompt_jobs, return_exceptions=True)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if feature_task:
                feature_task.cancel()
                with suppress(asyncio.CancelledError):
                    await feature_task
            for studio_task in studio_tasks: studio_task.cancel()
            await asyncio.gather(*studio_tasks, return_exceptions=True)
            store.close()
            lock.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(feature_routes(lambda: features))
    app.include_router(work_routes(lambda: pipeline))

    @app.exception_handler(FeatureConflict)
    async def feature_conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    allowed_origins = {'127.0.0.1:5174', 'localhost:5174', '127.0.0.1:8002', 'localhost:8002', 'testserver'}

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD') and origin and urlparse(origin).netloc not in allowed_origins:
            return JSONResponse({'detail': 'This workspace accepts local requests only'}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.get('/workspace/agents')
    async def agents_catalog():
        return catalog(store, features, reviews)

    @app.get('/workspace/review/state')
    async def review_state():
        return {'runs': reviews.list(), 'fixtures': reviews.fixtures(), 'tools': TOOL_CATALOG,
                'enabled': service.enabled, 'credential_source': service.credential_source}

    @app.post('/workspace/project/validate')
    async def validate_agent():
        project = store.project_snapshot()
        return {**await validate_code(project['files']), 'revision': project['revision'], 'version': project['version']}

    @app.post('/workspace/review/runs')
    async def start_review(request: ReviewRequest):
        source = request.source.model_dump(exclude_defaults=True)
        if source['kind'] == 'github':
            parse_pr_url(source.get('url', ''))
        else:
            reviews.fixture(source.get('fixture', ''))
        return await service.create(request.id, source, request.author, request.revision)

    @app.get('/workspace/review/runs/{ident}')
    async def get_review(ident: str):
        return {**reviews.get(ident), 'comments': reviews.comments(ident)}

    @app.post('/workspace/review/runs/{ident}/cancel')
    async def cancel_review(ident: str):
        await service.cancel(ident)
        return reviews.summary(reviews.get(ident))

    @app.post('/workspace/review/runs/{ident}/fixture')
    async def save_fixture(ident: str, request: FixtureRequest):
        fixture = reviews.save_fixture(ident, request.name)
        publish({'type': 'review-fixtures'})
        return {key: value for key, value in fixture.items() if key != 'evidence'}

    @app.post('/workspace/review/runs/{ident}/comments')
    async def add_comment(ident: str, request: CommentRequest):
        if not request.text.strip():
            raise ValueError('Comment cannot be empty')
        comments = reviews.add_comment(request.id, ident, request.author, request.text.strip(), request.finding)
        publish({'type': 'review-comment', 'run_id': ident})
        return comments

    @app.get('/workspace/review/compare')
    async def compare_reviews(first: str, second: str):
        return reviews.compare(first, second)

    @app.post('/workspace/review/tools/preview')
    async def preview_tool(request: PreviewRequest):
        return evidence_tool(reviews.fixture(request.fixture)['evidence'], request.tool, request.arguments)

    @app.get('/workspace/health')
    async def health():
        return {'ok': True, 'revision': store.revision}

    @app.post('/workspace/format')
    async def format_source(request: FormatRequest):
        # Pure computation: the browser checks that the snapshot is still current,
        # then submits the changes through the usual collaborative edit protocol.
        try:
            return await asyncio.to_thread(format_python, request.source)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.websocket('/workspace/connect')
    async def connect(socket: WebSocket):
        # Local demo only: do not let unrelated web pages connect to the local file host.
        from urllib.parse import urlparse
        origin = socket.headers.get('origin')
        if origin and urlparse(origin).netloc not in {
            '127.0.0.1:5174', 'localhost:5174', '127.0.0.1:8002', 'localhost:8002', 'testserver',
        }:
            await socket.close(code=1008)
            return
        await socket.accept()
        ident = uuid.uuid4().hex
        queue = asyncio.Queue(maxsize=256)
        name = (socket.query_params.get('name') or 'Collaborator')[:40].strip() or 'Collaborator'
        project_key = socket.query_params.get('project', 'review')
        chosen_host = studio_hosts.get(project_key.split(':', 1)[1]) if project_key.startswith('agent:') else feature_host if project_key == 'feature' else host
        peers[ident] = {'socket': socket, 'queue': queue, 'name': name, 'cursor': '', 'file': 'agent.py'}
        queue.put_nowait({'type': 'snapshot', **store.snapshot(), 'self': ident,
                         'events': store.events(), 'agent_enabled': with_agent,
                         'agent_error': chosen_host.error if chosen_host else None})
        publish(presence())

        async def send():
            while True:
                await socket.send_json(await queue.get())

        sender = asyncio.create_task(send())
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(socket.receive_text(), PRESENCE_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    await socket.close(code=1001, reason='Workspace presence expired')
                    break
                if len(raw.encode('utf-8')) > MAX_MESSAGE_BYTES:
                    raise ValueError('Message too large')
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('Expected a message object')
                try:
                    if data.get('type') == 'edit':
                        request = Edit.model_validate(data)
                        result = store.edit(request.id, request.operation, request.author, file=request.file)
                        publish({'type': 'operation', 'id': request.id, **result, 'events': store.events()})
                    elif data.get('type') == 'presence':
                        peers[ident]['name'] = str(data.get('name', name))[:40].strip() or name
                        peers[ident]['cursor'] = str(data.get('cursor', ''))[:120]
                        peers[ident]['file'] = str(data.get('file', 'app.py'))[:100]
                        publish(presence())
                    elif data.get('type') == 'prompt':
                        request = Prompt.model_validate(data)
                        if not chosen_host:
                            raise ValueError('Agent is disabled in collaboration-only mode')
                        # Sending must not block this editor's text operations.
                        async def submit(request=request):
                            try:
                                await asyncio.wait_for(chosen_host.prompt(request.id, request.author, request.text), timeout=15)
                                queue.put_nowait({'type': 'prompt-ack', 'id': request.id})
                            except Exception:
                                queue.put_nowait({'type': 'error', 'id': request.id, 'kind': 'prompt',
                                                  'message': 'Could not send to the agent. Check the worker, then retry.'})
                        job = asyncio.create_task(submit())
                        prompt_jobs.add(job)
                        job.add_done_callback(prompt_jobs.discard)
                    elif data.get('type') == 'ping':
                        queue.put_nowait({'type': 'pong'})
                except (ValueError, TypeError, KeyError) as exc:
                    queue.put_nowait({'type': 'error', 'id': data.get('id'), 'kind': data.get('type'),
                                     'message': str(exc)})
                except (OSError, sqlite3.Error):
                    logging.exception('Could not persist the workspace edit')
                    queue.put_nowait({'type': 'error', 'id': data.get('id'), 'kind': data.get('type'),
                                     'message': 'Local save failed. Your draft is retained; check the host logs, then reconnect to retry.'})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            peers.pop(ident, None)
            sender.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await sender
            publish(presence())

    prompt_jobs: set[asyncio.Task] = set()
    return app


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(with_agent=os.environ.get('WORKSPACE_NO_AGENT') != '1'),
                host='127.0.0.1', port=8002, ws_max_size=MAX_MESSAGE_BYTES)


if __name__ == '__main__':
    main()
