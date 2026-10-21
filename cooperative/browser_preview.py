"""Read-only built previews and bounded browser observations, owned by the host."""
import asyncio
import base64
from collections import deque
from contextlib import nullcontext, suppress
import json
from pathlib import Path
import shutil
import socket
import time
from urllib.parse import urlsplit
import uuid

import uvicorn
from pydantic import BaseModel, Field

from .preview_gateway import gateway


class BrowserRequest(BaseModel):
    action: str = Field(pattern='^(open|inspect|navigate|scroll)$')
    path: str = Field(default='/', max_length=1000)
    target: str = Field(default='', max_length=200)
    width: int = Field(default=1440, ge=320, le=1920)
    height: int = Field(default=900, ge=320, le=1200)


def supported(feature):
    """First adapter: Code Studio's Vite frontend and audited backend read routes."""
    try:
        root = Path(feature['worktree'])
        package = json.loads((root / 'package.json').read_text())
        return package.get('name') == 'code-studio' and bool(package.get('scripts', {}).get('build')) and (root / 'ui/index.html').is_file()
    except (OSError, ValueError):
        return False


def relative_path(path):
    parsed = urlsplit(path)
    if not path.startswith('/') or path.startswith('//') or parsed.netloc or parsed.scheme or '\\' in path or '%' in parsed.path:
        raise ValueError('Choose a local preview path, such as /?view=work.')
    if parsed.path.startswith(('/workspace', '/api', '/harness', '/__preview')) or '..' in parsed.path.split('/'):
        raise ValueError('Choose a frontend page, not a backend endpoint.')
    return path


class BrowserPreviewService:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.backend = None
        self.snapshot = lambda: {}
        self.root = pipeline.workspace.directory / 'browser-previews'
        self.instances = {}
        self.lock = asyncio.Lock()
        self.playwright = None
        self.browser = None
        self.reaper = None
        self.closing = False
        with pipeline.store.db:
            pipeline.store.db.execute('CREATE TABLE IF NOT EXISTS browser_evidence (id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, value TEXT NOT NULL)')
            pipeline.store.db.execute('CREATE TABLE IF NOT EXISTS browser_calls (id TEXT PRIMARY KEY, request TEXT NOT NULL)')

    async def status(self, ident):
        feature = self.pipeline.get(ident)
        current = await asyncio.to_thread(self.pipeline.checks.fingerprint, feature)
        rows = self.pipeline.store.db.execute('SELECT value FROM browser_evidence WHERE feature_id=? ORDER BY rowid DESC LIMIT 40', (ident,))
        evidence = []
        for row in rows:
            record = json.loads(row[0])
            evidence.append({**record, 'stale': record['source_hash'] != current or record['input_revision'] != feature['input_revision']})
        instance = self.instances.get(ident)
        return {'available': supported(feature), 'url': instance['url'] if instance else None,
                'source_hash': instance['hash'] if instance else None,
                'stale': bool(instance and (instance['hash'] != current or instance['input_revision'] != feature['input_revision'])), 'evidence': evidence}

    async def start(self, ident):
        async with self.lock:
            return await self._start(ident)

    async def _start(self, ident):
        if self.closing or self.backend is None:
            raise ValueError('Preview host is unavailable. Restart Code Studio.')
        feature = self.pipeline.get(ident)
        if not supported(feature):
            raise ValueError('Read-only previews currently support Code Studio’s Vite frontend. This project needs a preview adapter.')
        current = await asyncio.to_thread(self.pipeline.checks.fingerprint, feature)
        old = self.instances.get(ident)
        if old and old['hash'] == current and old['input_revision'] == feature['input_revision']:
            old['used'] = time.monotonic()
            return old
        if old: await self._close(ident)
        if len(self.instances) >= 2:
            await self._close(min(self.instances, key=lambda key: self.instances[key]['used']))
        # Build and copy while holding the same npm lock used by validation checks.
        npm_lock = self.pipeline.validation.npm_locks.setdefault(feature['worktree'], asyncio.Lock())
        async with npm_lock:
            dependencies = Path(feature['worktree']) / 'node_modules'
            installed = Path(feature['repository']) / 'node_modules'
            linked = not dependencies.exists() and not dependencies.is_symlink() and installed.is_dir()
            if linked: dependencies.symlink_to(installed.resolve(), target_is_directory=True)
            try:
                build = await self.pipeline.checks.run(feature, 'build')
            finally:
                if linked: dependencies.unlink(missing_ok=True)
            if build['status'] != 'passed':
                raise ValueError('Preview build failed: ' + build.get('output', '')[-3000:])
            if await asyncio.to_thread(self.pipeline.checks.fingerprint, feature) != current:
                raise ValueError('Source changed while building the preview. Try again after edits finish.')
            output = Path(feature['worktree']) / 'dist'
            if not (output / 'index.html').is_file():
                raise ValueError('The build did not produce dist/index.html.')
            destination = self.root / uuid.uuid4().hex
            await asyncio.to_thread(self._copy, output, destination)
        app = gateway(destination, self.backend, self.snapshot)
        listener = socket.socket()
        try:
            listener.bind(('127.0.0.1', 0)); listener.listen(64); listener.setblocking(False)
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off', ws_max_size=64000))
            server.capture_signals = nullcontext
            task = asyncio.create_task(server.serve(sockets=[listener]))
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done(): await task; raise ValueError('Preview server could not start.')
                    await asyncio.sleep(.05)
        except BaseException:
            listener.close()
            if 'task' in locals():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)
            shutil.rmtree(destination, ignore_errors=True)
            raise
        instance = {'hash': current, 'url': f'http://127.0.0.1:{port}', 'server': server, 'task': task,
                    'listener': listener, 'directory': destination, 'used': time.monotonic(), 'sessions': {},
                    'input_revision': feature['input_revision']}
        self.instances[ident] = instance
        if self.reaper is None: self.reaper = asyncio.create_task(self._expire())
        return instance

    @staticmethod
    def _copy(source, destination):
        if source.is_symlink(): raise ValueError('Preview output must not be a symbolic link.')
        files = list(source.rglob('*'))
        if any(p.is_symlink() for p in files) or sum(p.stat().st_size for p in files if p.is_file()) > 50_000_000:
            raise ValueError('Preview output must be smaller than 50 MB and contain no symbolic links.')
        shutil.copytree(source, destination)

    async def _session(self, instance, owner):
        from playwright.async_api import async_playwright
        if self.browser is None:
            self.playwright = await async_playwright().start()
            try:
                self.browser = await self.playwright.chromium.launch()
            except Exception as exc:
                await self.playwright.stop(); self.playwright = None
                raise ValueError('Preview browser unavailable. Run uv run playwright install chromium on the host.') from exc
        if owner in instance['sessions']: return instance['sessions'][owner]
        # Only one agent session per preview; humans use independent browser tabs.
        for session in instance['sessions'].values(): await session['context'].close()
        instance['sessions'].clear()
        context = await self.browser.new_context(service_workers='block', accept_downloads=False)
        errors = deque(maxlen=20)
        async def restrict(route):
            request = route.request
            if request.url.startswith(instance['url'] + '/') and request.method in ('GET', 'HEAD'):
                await route.continue_()
            else:
                errors.append('Blocked request: ' + request.method + ' ' + request.url[:200])
                await route.abort()
        await context.route('**/*', restrict)
        page = await context.new_page()
        page.set_default_timeout(5000)
        page.on('pageerror', lambda error: errors.append(str(error)[:500]))
        page.on('console', lambda message: errors.append(message.text[:500]) if message.type == 'error' else None)
        page.on('response', lambda response: errors.append(f'HTTP {response.status}: {response.url[:200]}') if response.status >= 400 else None)
        page.on('requestfailed', lambda request: errors.append(f'{request.failure}: {request.url[:200]}'))
        page.on('dialog', lambda dialog: dialog.dismiss())
        session = {'context': context, 'page': page, 'errors': errors, 'opened': False}
        instance['sessions'][owner] = session
        return session

    async def observe(self, ident, owner, request, receipt_id=None):
        request = BrowserRequest.model_validate(request)
        relative_path(request.path)
        async with self.lock:
            # Store observations, not live browser sessions, durably. Never replay a toggle on retry.
            if receipt_id:
                row = self.pipeline.store.db.execute('SELECT value FROM browser_evidence WHERE id=? AND feature_id=?', (receipt_id, ident)).fetchone()
                if row:
                    record = json.loads(row[0])
                    if record['request'] != request.model_dump(): raise ValueError('Browser request ID reused with different arguments.')
                    return self._with_image(record)
                pending = self.pipeline.store.db.execute('SELECT 1 FROM browser_calls WHERE id=?', (receipt_id,)).fetchone()
                if pending:
                    raise ValueError('The previous browser action was interrupted. Open the page with a new call before continuing; its outcome is unknown.')
                with self.pipeline.store.db:
                    self.pipeline.store.db.execute('INSERT INTO browser_calls VALUES (?, ?)', (receipt_id, request.model_dump_json()))
            instance = await self._start(ident)
            session = await self._session(instance, owner)
            page = session['page']
            await page.set_viewport_size({'width': request.width, 'height': request.height})
            try:
                if request.action == 'open':
                    await page.goto(instance['url'] + request.path, wait_until='networkidle', timeout=15000)
                    session['opened'] = True
                elif not session['opened']:
                    raise ValueError('Browser session expired or restarted. Open the page again before navigating.')
                elif request.action == 'navigate':
                    # Only links and disclosure/tab controls; never arbitrary buttons or form fields.
                    allowed = page.locator('a[href],summary,[role=tab],button[aria-expanded],[data-preview-navigation] button,button.theme-toggle')
                    locator = allowed.filter(has_text=request.target)
                    if request.target and await locator.count() == 0:
                        locator = allowed.and_(page.get_by_role('button', name=request.target, exact=True))
                    if not request.target or await locator.count() != 1:
                        raise ValueError('Choose one unique link, tab, or disclosure label from the snapshot.')
                    await locator.click()
                elif request.action == 'scroll':
                    if request.target not in ('up', 'down'): raise ValueError('Scroll target must be up or down.')
                    await page.mouse.wheel(0, request.height * (.8 if request.target == 'down' else -.8))
                await page.wait_for_timeout(200)
                snapshot = (await page.locator('body').aria_snapshot())[:16000]
                screenshot = await page.screenshot(type='jpeg', quality=65, animations='disabled')
            except ValueError: raise
            except Exception as exc:
                raise ValueError('Browser preview could not complete this action: ' + str(exc)[:700]) from exc
            if len(screenshot) > 500_000: raise ValueError('Screenshot exceeded the 500 KB evidence limit. Use a smaller viewport.')
            eid = receipt_id or uuid.uuid4().hex
            # IDs supplied by callbacks are digested before reaching this method.
            if not all(c in '0123456789abcdef' for c in eid): raise ValueError('Invalid evidence ID.')
            images = self.root / 'evidence'; images.mkdir(parents=True, exist_ok=True)
            (images / (eid + '.jpg')).write_bytes(screenshot)
            feature = self.pipeline.get(ident)
            record = {'id': eid, 'feature_id': ident, 'run_id': owner, 'source_hash': instance['hash'],
                'input_revision': instance['input_revision'], 'created_at': time.time(), 'request': request.model_dump(),
                'stale': await asyncio.to_thread(self.pipeline.checks.fingerprint, feature) != instance['hash'] or feature['input_revision'] != instance['input_revision'],
                'path': page.url.removeprefix(instance['url']), 'snapshot': snapshot, 'errors': list(session['errors']),
                'screenshot': f'/workspace/work/{ident}/browser/evidence/{eid}',
                'limitation': 'Read-only frontend against live backend data; saving and new backend behavior are not verified.'}
            with self.pipeline.store.db:
                self.pipeline.store.db.execute('INSERT INTO browser_evidence VALUES (?, ?, ?)', (eid, ident, json.dumps(record)))
            self.pipeline.announce(ident)
            return self._with_image(record)

    def image_path(self, ident, eid):
        if not self.pipeline.store.db.execute('SELECT 1 FROM browser_evidence WHERE id=? AND feature_id=?', (eid, ident)).fetchone():
            raise ValueError('Browser evidence not found.')
        return self.root / 'evidence' / (eid + '.jpg')

    def _with_image(self, record):
        return {**record, 'image': 'data:image/jpeg;base64,' + base64.b64encode(self.image_path(record['feature_id'], record['id']).read_bytes()).decode()}

    async def close(self, ident):
        async with self.lock: await self._close(ident)

    async def _close(self, ident):
        instance = self.instances.pop(ident, None)
        if not instance: return
        for session in instance['sessions'].values(): await session['context'].close()
        instance['server'].should_exit = True
        try: await asyncio.wait_for(instance['task'], 3)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            instance['task'].cancel(); await asyncio.gather(instance['task'], return_exceptions=True)
        instance['listener'].close()
        shutil.rmtree(instance['directory'], ignore_errors=True)

    async def _expire(self):
        while True:
            await asyncio.sleep(30)
            async with self.lock:
                for ident in list(self.instances):
                    if time.monotonic() - self.instances[ident]['used'] > 1800: await self._close(ident)

    async def stop(self):
        self.closing = True
        if self.reaper:
            self.reaper.cancel()
            with suppress(asyncio.CancelledError): await self.reaper
        async with self.lock:
            for ident in list(self.instances): await self._close(ident)
            if self.browser: await self.browser.close()
            if self.playwright: await self.playwright.stop()
            self.browser = self.playwright = self.reaper = None
