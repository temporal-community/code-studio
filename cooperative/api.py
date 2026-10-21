import asyncio
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from sdlc.models import digest
from sdlc.support_catalog import support_details, SUPPORT_AGENTS
from workspace.support_instructions import InstructionEdit, InstructionConflict

from .definitions import DEFINITIONS, STAGES
from .models import PipelineAction, IntegrationResolution, WorkCreate, PipelineContribution, validate_project
from .agent_models import ApprovalRequest, FeedbackRequest
from .library import entry, library_files, read_library
from .experiments import PlaybookEdit, ExperimentCreate, ExperimentStart, Assessment
from .suggestions import SuggestionRequest, SuggestionSourceError, suggest
from .clarification import ClarificationRequest

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def public_feature(feature):
    result = {k: v for k, v in feature.items() if k not in ('source_manifest', 'request_hash', 'experiment_config')}
    result['pipeline'] = {**feature['pipeline'], 'runs': [
        {k: v for k, v in run.items() if k not in ('context', 'library')} for run in feature['pipeline']['runs']]}
    return result


def work_routes(service):
    router = APIRouter(prefix='/workspace')

    @router.get('/work/{ident}/browser')
    async def browser_status(ident: str):
        return await service().browser_previews.status(ident)

    @router.post('/work/{ident}/browser/start')
    async def start_browser_preview(ident: str):
        await service().browser_previews.start(ident)
        return await service().browser_previews.status(ident)

    @router.post('/work/{ident}/browser/stop')
    async def stop_browser_preview(ident: str):
        await service().browser_previews.close(ident)
        return await service().browser_previews.status(ident)

    @router.get('/work/{ident}/browser/evidence/{evidence_id}')
    async def browser_evidence(ident: str, evidence_id: str):
        return FileResponse(service().browser_previews.image_path(ident, evidence_id), media_type='image/jpeg',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/support-agents/{agent_id}')
    async def support_agent(agent_id: str):
        if agent_id not in SUPPORT_AGENTS:
            raise HTTPException(404, 'Unknown support agent')
        return support_details(agent_id, service().workspace)

    @router.post('/support-agents/{agent_id}')
    async def save_support_agent(agent_id: str, request: InstructionEdit):
        if agent_id not in SUPPORT_AGENTS:
            raise HTTPException(404, 'Unknown support agent')
        prompt = next((p for p in support_details(agent_id)['prompts'] if p['id'] == request.target), None)
        if prompt is None:
            raise HTTPException(422, 'Unknown instructions target')
        workspace = service().workspace
        try:
            workspace.support_instructions.save(agent_id, request, prompt['content'])
        except InstructionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return support_details(agent_id, workspace)

    @router.get('/platform')
    async def platform():
        s = service(); features = []
        for item in s.store.list():
            f = s.store.get(item['id'])
            if 'pipeline' in f:
                p = f['pipeline']
                features.append({**item, 'pipeline_status': p['status'], 'current_stage': p['current_stage'],
                    'experiment_arm': f.get('experiment_arm'), 'experiment_id': f.get('experiment_id'),
                    'stages': p['stages'], 'updated_at': f['updated_at'], 'working_directory': f['repository'],
                    'summary': next((r['result']['summary'] for r in reversed(p['runs']) if r.get('result')), f['description'][:300])})
        return {'features': features, 'default_directory': str(ROOT), 'enabled': s.enabled,
                'stages': [{'id': key, 'name': DEFINITIONS[key]['name'], 'verb': DEFINITIONS[key]['verb']} for key in STAGES]}

    @router.get('/platform/revisions')
    async def revisions(feature_id: str = '', include_files: bool = False):
        # Small catch-up response; full snapshots are fetched only when changed.
        state = await platform()
        result = {'platform': digest(state)}
        if feature_id:
            s = service()
            feature = s.get(feature_id)
            result['feature'] = digest(public_feature(feature))
            if include_files:
                try:
                    result['files'] = await asyncio.to_thread(s.checks.fingerprint, feature)
                except (OSError, ValueError) as exc:
                    result['files_error'] = str(exc)
        return result

    @router.post('/platform/suggestion')
    async def suggestion(request: SuggestionRequest):
        s = service()
        if not s.enabled:
            raise HTTPException(503, 'Start just dev with an OpenAI key to ask the feature suggestion agent.')
        try:
            return await asyncio.wait_for(suggest(s, ROOT, request), timeout=190)
        except TimeoutError as exc:
            raise HTTPException(504, 'The feature suggestion agent timed out. Your draft is unchanged; try again.') from exc
        except SuggestionSourceError as exc:
            raise HTTPException(400, 'Check the working directory and starting revision, then try again.') from exc
        except Exception as exc:
            logger.exception('Code Studio improvement suggestion failed')
            raise HTTPException(503, 'Could not suggest a feature. Your draft is unchanged; check the workspace and worker logs, then try again.') from exc

    @router.get('/agents/{agent_id}/project')
    async def project(agent_id: str):
        return service().workspace.agent_project_snapshot(agent_id)

    @router.get('/playbook')
    async def playbook():
        return service().experiments.playbook()

    @router.post('/playbook')
    async def save_playbook(request: PlaybookEdit):
        return service().experiments.edit_playbook(request.model_dump())

    @router.get('/experiments')
    async def experiments(feature_id: str):
        return {'experiments': service().experiments.list(feature_id)}

    @router.post('/experiments')
    async def create_experiment(request: ExperimentCreate):
        return await service().experiments.create(request.model_dump())

    @router.get('/experiments/{ident}')
    async def experiment(ident: str):
        return await service().experiments.compare(ident)

    @router.post('/experiments/{ident}/start')
    async def start_experiment(ident: str, request: ExperimentStart):
        return await service().experiments.start(ident, request.author)

    @router.post('/experiments/{ident}/assess/{arm}')
    async def assess_experiment(ident: str, arm: str, request: Assessment):
        return await service().experiments.assess(ident, arm, request.model_dump())

    @router.post('/agents/{agent_id}/validate')
    async def validate(agent_id: str):
        project = service().workspace.agent_project_snapshot(agent_id)
        return {**await validate_project(agent_id, project['files']), 'version': project['version'], 'revision': project['revision']}

    @router.post('/work')
    async def create(request: WorkCreate):
        return public_feature(await service().create(request.model_dump()))

    @router.get('/work/{ident}')
    async def detail(ident: str):
        return public_feature(service().get(ident))

    @router.get('/work/{ident}/library')
    async def library(ident: str, run_id: str | None = None):
        files = library_files(service().get(ident), run_id)
        return {'files': [entry(path, content) for path, content in sorted(files.items())]}

    @router.post('/work/{ident}/runs/{run_id}/clarify')
    async def clarify(ident: str, run_id: str, request: ClarificationRequest):
        s = service()
        if not s.enabled:
            raise HTTPException(503, 'Start just dev with an OpenAI key to ask the agent.')
        return await s.clarifications.ask(ident, run_id, request)

    @router.post('/work/{ident}/runs/{run_id}/requirements')
    async def submit_requirements(ident: str, run_id: str, request: ClarificationRequest):
        return public_feature(await service().contribute(ident, {
            **request.model_dump(), 'kind': 'requirement', 'clarification_run_id': run_id,
        }))

    @router.get('/work/{ident}/library/file')
    async def library_file(ident: str, path: str, run_id: str | None = None):
        return read_library(service().get(ident), path, run_id)

    @router.post('/work/{ident}/control/{action}')
    async def control(ident: str, action: str, request: PipelineAction):
        return public_feature(await service().control(ident, action, request.model_dump()))

    @router.post('/work/{ident}/inputs')
    async def contribute(ident: str, request: PipelineContribution):
        return public_feature(await service().contribute(ident, request.model_dump()))

    @router.post('/work/{ident}/approvals')
    async def approve(ident: str, request: ApprovalRequest):
        return public_feature(await service().approve_managed(ident, request.model_dump()))

    @router.post('/work/{ident}/feedback')
    async def feedback(ident: str, request: FeedbackRequest):
        return public_feature(await service().feedback_managed(ident, request.model_dump()))

    @router.get('/work/{ident}/release')
    async def download(ident: str):
        s = service(); release = s.get(ident)['pipeline'].get('release')
        if not release: raise ValueError('No local release preview has been created')
        path = Path(release['path'])
        if not path.resolve().is_relative_to((s.git.directory / 'releases').resolve()) or not path.is_file():
            raise ValueError('Release preview is unavailable')
        return FileResponse(path, media_type='application/zip', filename='code-studio-feature-' + ident[:8] + '.zip')

    @router.post('/work/{ident}/apply')
    async def apply(ident: str, request: PipelineAction):
        return await service().apply(ident, request.model_dump())

    @router.get('/work/{ident}/integration')
    async def integration(ident: str):
        return await service().integration(ident)

    @router.get('/work/{ident}/integration/conflict')
    async def integration_conflict(ident: str, path: str):
        return await service().integration_conflict(ident, path)

    @router.post('/work/{ident}/integration/resolve')
    async def resolve_integration(ident: str, request: IntegrationResolution):
        return await service().integration(ident, request.model_dump(), 'resolve')

    @router.post('/work/{ident}/integration/{action}')
    async def integration_action(ident: str, action: str, request: PipelineAction):
        return await service().integration(ident, request.model_dump(), action)

    return router
