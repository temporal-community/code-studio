import ast
from typing import Literal
from pydantic import Field
from features.models import StrictModel, FeatureCreate, Contribution
from .definitions import DEFINITIONS, files_for

StageId = Literal['ideation', 'planner', 'design', 'development', 'review', 'deployment']


class WorkCreate(FeatureCreate):
    source_mode: Literal['current', 'git'] = 'current'


class PipelineContribution(Contribution):
    kind: Literal['comment', 'requirement', 'question', 'decision', 'feedback'] = 'comment'


class StageSettings(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    model: Literal['gpt-6-luna', 'gpt-6.1-sol'] = 'gpt-6-luna'
    tools: list[str] = Field(max_length=6)
    max_turns: int = Field(default=12, ge=2, le=40)
    tool_mode: Literal['code', 'individual'] = 'code'


class Deliverable(StrictModel):
    title: str = Field(max_length=200)
    body: str = Field(max_length=6000)


class StageFinding(StrictModel):
    title: str = Field(max_length=200)
    severity: Literal['low', 'medium', 'high']
    path: str = Field(max_length=300)
    line: int | None
    explanation: str = Field(max_length=3000)


class StageResult(StrictModel):
    summary: str = Field(max_length=5000)
    verdict: Literal['ready', 'blocked', 'changes_requested']
    deliverables: list[Deliverable] = Field(max_length=12)
    acceptance_criteria: list[str] = Field(max_length=30)
    questions: list[str] = Field(max_length=15)
    risks: list[str] = Field(max_length=20)
    findings: list[StageFinding] = Field(max_length=30)
    changed_files: list[str] = Field(max_length=50, description=(
        'Files successfully written by THIS stage run, not files changed by upstream agents '
        'or merely inspected. Use [] for read-only stages such as Review. The host derives '
        'the saved list from successful write receipts.'))
    handoff: str = Field(max_length=6000)


class StageExecution(StrictModel):
    """A frozen agent definition and the authoritative feature/stage handoff."""
    stage: StageId
    context: dict
    project: dict


class StudioMessage(StrictModel):
    """A teammate's request to customize one agent in the shared Studio."""
    text: str
    agent_id: StageId
    instructions: str | None = Field(default=None, min_length=1, max_length=20000)
    instructions_version: str | None = None


class PipelineAction(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    author: str = Field(min_length=1, max_length=40)
    revision: int = Field(ge=1)
    stage: StageId | None = None
    note: str = Field(default="", max_length=4000)


class IntegrationResolution(PipelineAction):
    integration_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    path: str = Field(min_length=1, max_length=300)
    choice: Literal['local', 'feature', 'edit']
    content: str | None = Field(default=None, max_length=100000)


async def validate_project(stage, files):
    from temporal_agent_harness.harness import agent
    from agent_dag.stage_agent import stage_flow
    if stage not in DEFINITIONS:
        raise ValueError('Unknown agent')
    script, prompt, config = files_for(stage)
    for name in (script, prompt, config):
        if name not in files or not files[name].strip():
            raise ValueError('Missing or empty agent file: ' + name)
    settings = StageSettings.model_validate_json(files[config])
    if len(set(settings.tools)) != len(settings.tools) or set(settings.tools) - set(DEFINITIONS[stage]['tools']):
        raise ValueError('Tools must be unique allowed tools: ' + ', '.join(DEFINITIONS[stage]['tools']))
    if 'stage_write_file' in settings.tools and 'stage_read_file' not in settings.tools:
        raise ValueError('Enable stage_read_file when enabling writes')
    tree = ast.parse(files[script])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(n.name != 'asyncio' for n in node.names):
            raise ValueError('Only import asyncio and registered tools are supported')
        if isinstance(node, ast.ImportFrom):
            raise ValueError('Use registered tool functions instead of imports')
    error = await agent.code_mode_type_check(stage_flow(), files[script])
    if error:
        raise ValueError(error)
    return {'ok': True, 'message': DEFINITIONS[stage]['name'] + ' code, prompt, settings, and tool contract are valid.', 'settings': settings.model_dump()}
