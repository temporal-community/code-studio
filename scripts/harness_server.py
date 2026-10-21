"""Packaged Harness UI with a narrow adapter for Code Studio workflow deep links.

Harness 0.6.0 otherwise selects a different managed session for untracked or
closed workflows. Patch a temporary asset copy; never modify the installation.
"""
from pathlib import Path
import hashlib
import shutil
import tempfile

from fastapi import HTTPException
from fastapi.routing import APIRoute
from temporalio.service import RPCError, RPCStatusCode
from temporal_agent_harness.ui import packaged_ui_dist
from temporal_agent_harness.web import create_agent_harness_app
from temporal_agent_harness.web.registry import load_agent_registries

ROOT = Path(__file__).resolve().parents[1]


def prepare_ui(destination):
    source = packaged_ui_dist()
    if source is None:
        raise RuntimeError('Install the pinned temporal-agent-harness[ui] dependency.')
    shutil.copytree(source, destination, dirs_exist_ok=True)
    replacements = {
        # A directly opened child is the root of this view. Upstream uses an
        # agent-ID suffix heuristic and drops its frames from visibleFrames.
        # Only explicitly discovered descendants belong to another runtime.
        'd=M0(l.data)?"parent":"subagent"':
            'd=c?"subagent":"parent"',
        'async listSessions(){return Zr(pr("sessions"))}':
            'async listSessions(){const id=U6();return Zr(pr("debug-sessions"+(id?"?workflow_id="+encodeURIComponent(id):"")))}',
        'return r&&!(O6(r)&&D6(t,r))?r:[...t]': 'return e?r??null:[...t]',
        'if(!s&&o&&(s=await pe(this,ve,wv).call(this,o)),s)this.session=s;':
            'if(!s&&o)throw new Error("The requested workflow is unavailable.");if(s)this.session=s;',
    }
    scripts = list((destination / 'assets').glob('*.js'))
    bundle = next((p for p in scripts if 'async listSessions(){return Zr(pr("sessions"))}' in p.read_text()), None)
    if bundle is None:
        raise RuntimeError('Harness UI compatibility changed; update the 0.6.0 deep-link adapter.')
    content = bundle.read_text()
    for old, new in replacements.items():
        if content.count(old) != 1:
            raise RuntimeError('Harness UI compatibility changed; update the 0.6.0 deep-link adapter.')
        content = content.replace(old, new)
    # The upstream filename hashes the unpatched code. A distinct filename
    # prevents cached upstream assets from bypassing our compatibility adapter.
    patched = bundle.with_name(f'studio-{hashlib.sha256(content.encode()).hexdigest()[:16]}.js')
    patched.write_text(content)
    index = destination / 'index.html'
    index.write_text(index.read_text().replace(bundle.name, patched.name))



def create_app(static_dir):
    app = create_agent_harness_app(registry=load_agent_registries([ROOT / 'agents.toml']), static_dir=static_dir)
    @app.middleware('http')
    async def fresh_debugger_html(request, call_next):
        response = await call_next(request)
        if request.url.path == '/':
            response.headers['Cache-Control'] = 'no-store'
        return response

    sessions = next(route.endpoint for route in app.routes if isinstance(route, APIRoute) and route.path == '/api/sessions' and 'GET' in route.methods)

    @app.get('/api/debug-sessions')
    async def debug_sessions(workflow_id: str | None = None):
        if not workflow_id:
            return await sessions()
        try:
            execution = await app.state.temporal.get_workflow_handle(workflow_id).describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, 'The requested workflow was not found') from exc
            raise
        return [{'workflow_id': execution.id, 'created_at': execution.start_time.timestamp(),
            'label': execution.workflow_type, 'agent_workflow_type': execution.workflow_type,
            'initial_user_message': None, 'is_discovered': True,
            'execution_status': execution.status.name, 'closed': execution.status.name != 'RUNNING'}]
    return app


if __name__ == '__main__':
    import uvicorn
    with tempfile.TemporaryDirectory(prefix='code-studio-harness-') as directory:
        prepare_ui(Path(directory))
        uvicorn.run(create_app(directory), host='127.0.0.1', port=8001)
