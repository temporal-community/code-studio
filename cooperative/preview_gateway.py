"""Local static preview with an explicit read-only backend surface."""
import asyncio
import mimetypes
from pathlib import Path
import re

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse

# These routes were audited for reads. New backend routes are denied by default.
READ_PATHS = re.compile(r'/workspace/(?:health|agents|platform(?:/revisions)?|playbook|'
    r'support-agents/[^/]+|agents/[^/]+/project|review/state|review/runs/[^/]+|'
    r'work/[^/]+(?:/library(?:/file)?|/browser(?:/evidence/[a-f0-9]+)?)?|features/[^/]+/(?:files|file|diff(?:/files|/file)?))\Z')
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'; sandbox allow-scripts allow-same-origin")

# This is a usability layer. The gateway, CSP, and browser request policy enforce writes.
GUARD = r'''
try { sessionStorage.setItem('code-studio:name', 'Preview'); } catch {}
const blocked = /^(save|submit|approve|reject|decline|delete|remove|apply|merge|start feature|suggest feature|post|run |rerun|resume|pause|cancel|retry|restore|reset|send|ask agent|validate|create|adopt|publish|deploy|build preview|refresh preview|stop preview)/i;
function protect() {
  document.querySelectorAll('button,input[type=submit]').forEach(el => {
    const label = (el.textContent || el.value || '').trim();
    const startsForm = /^start feature$/i.test(label) && !el.form;
    if ((el.type === 'submit' && el.form) || (blocked.test(label) && !startsForm)) {
      if (!el.disabled) el.disabled = true;
      el.title = 'Unavailable in read-only preview';
    }
  });
  document.querySelectorAll('textarea,[contenteditable=true]').forEach(el => {
    if (el.tagName === 'TEXTAREA') { if (!el.readOnly) el.readOnly = true; }
    else if (el.contentEditable !== 'false') el.contentEditable = 'false';
  });
  document.querySelectorAll('a[href]').forEach(el => {
    const url = new URL(el.href, location.href);
    if (url.origin !== location.origin) { el.removeAttribute('href'); el.setAttribute('aria-disabled', 'true'); }
    el.removeAttribute('target'); el.removeAttribute('download');
  });
}
document.addEventListener('submit', e => { e.preventDefault(); e.stopImmediatePropagation(); }, true);
document.addEventListener('click', e => {
  const link = e.target.closest?.('a[href]');
  if (link && new URL(link.href, location.href).origin !== location.origin) {
    e.preventDefault(); e.stopImmediatePropagation();
  }
}, true);
addEventListener('DOMContentLoaded', () => {
  const banner = document.createElement('aside');
  banner.setAttribute('role', 'status'); banner.id = 'code-studio-preview-banner';
  banner.textContent = 'Read-only preview · Live backend data · Changes cannot be saved';
  document.body.prepend(banner);
  const style = document.createElement('style');
  style.textContent = 'body{display:flex;flex-direction:column;height:100dvh}#app{flex:1;min-height:0}#app>:is(.work-app,.agents-app,.workspace-app,.app-shell,.app){height:100%}#code-studio-preview-banner{flex:none;position:sticky;top:0;z-index:2147483647;padding:10px 16px;background:var(--color-surface-primary,Canvas);color:var(--color-content-primary,CanvasText);border-bottom:2px solid var(--color-border-primary,GrayText);font:14px/1.5 system-ui;text-align:center}';
  document.head.append(style);
  protect(); new MutationObserver(protect).observe(document.body, {childList:true,subtree:true,attributes:true,attributeFilter:['disabled','href','contenteditable','readonly']});
});
'''


def gateway(directory: Path, backend, snapshot):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    directory = directory.resolve()

    @app.middleware('http')
    async def guard(request: Request, call_next):
        # No CORS, DNS rebinding, service workers, cookies, or redirected upstream requests.
        if request.url.hostname not in ('127.0.0.1', 'localhost', 'testserver'):
            return Response(status_code=403)
        if request.method not in ('GET', 'HEAD'):
            return JSONResponse({'detail': 'Read-only preview: changes are disabled.'}, status_code=403)
        response = await call_next(request)
        response.headers.update({'Content-Security-Policy': CSP, 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()'})
        return response

    @app.websocket('/workspace/connect')
    async def connect(socket: WebSocket):
        if socket.headers.get('origin') != str(socket.url).replace('ws://', 'http://').split('/workspace/')[0]:
            await socket.close(code=1008); return
        await socket.accept()
        await socket.send_json({'type': 'snapshot', **snapshot(), 'self': 'preview', 'events': [],
                                'agent_enabled': False, 'agent_error': None})
        try:
            while True:
                try:
                    data = await asyncio.wait_for(socket.receive_json(), 5)
                except asyncio.TimeoutError:
                    await socket.send_json({'type': 'feature-changed'}); continue
                if data == {'type': 'ping'}:
                    await socket.send_json({'type': 'pong'})
                else:
                    await socket.send_json({'type': 'error', 'message': 'Read-only preview: changes are disabled.'})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass

    @app.api_route('/{path:path}', methods=['GET', 'HEAD'])
    async def read(path: str, request: Request):
        if '%' in path or '\\' in path or any(p in ('.', '..') for p in path.split('/')):
            return Response(status_code=403)
        route = '/' + path
        if route.startswith(('/workspace', '/api', '/harness')):
            if not READ_PATHS.fullmatch(route):
                return JSONResponse({'detail': 'This operation is unavailable in read-only preview.'}, status_code=403)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend), base_url='http://preview-backend',
                                         follow_redirects=False, timeout=10) as client:
                result = await client.get(route, params=request.query_params)
            media_type = result.headers.get('content-type', '').split(';')[0]
            if result.status_code != 200 or media_type not in ('application/json', 'image/jpeg'):
                return JSONResponse({'detail': 'The running backend could not provide this preview data.'}, status_code=502)
            return Response(result.content, media_type=media_type)
        if route == '/__preview_guard.js':
            return Response(GUARD, media_type='text/javascript')
        target = directory / path
        if not target.resolve().is_relative_to(directory) or target.is_symlink():
            return Response(status_code=403)
        if not target.is_file():
            if Path(path).suffix: return Response(status_code=404)
            target = directory / 'index.html'
        if target.suffix == '.html':
            html = target.read_text()
            html = html.replace('<head>', '<head><script src="/__preview_guard.js"></script>', 1)
            return Response(html, media_type='text/html')
        return Response(target.read_bytes(), media_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')

    return app
