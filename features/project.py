"""Editable feature-builder project and validation of its published Code Mode contract."""
import ast
import json
from typing import Literal

from pydantic import Field

from .models import StrictModel

FILE_TOOLS = ('feature_list_files', 'feature_read_file', 'feature_write_file')


class FeatureAgentSettings(StrictModel):
    name: str = Field(default='Feature builder', min_length=1, max_length=80)
    model: Literal['gpt-6-luna', 'gpt-6.1-sol'] = 'gpt-6-luna'
    tools: list[str] = Field(default_factory=lambda: list(FILE_TOOLS), max_length=3)
    max_turns: int = Field(default=35, ge=2, le=40)


PLANNER_PROMPT = '''You help a team turn a feature request into a concrete implementation plan.
Synthesize the brief, acceptance criteria, and every collaborator contribution. Account for
each contribution using its exact ID in input_coverage. Explain disagreements instead of
inventing agreement. Propose focused steps, affected files, acceptance criteria, a test plan,
scope exclusions, and risks. Only implementation-blocking questions belong in open_questions;
put optional considerations in risks. Prefer small changes that preserve existing behavior.
'''

CODER_PROMPT = '''Implement the approved feature plan with focused, maintainable code.
Read relevant source files before editing. Preserve existing behavior and collaborators' edits.
Include regression tests for the acceptance criteria. Prefer the project's existing conventions
and dependencies. If a write conflicts, reread the file and incorporate the collaborator's edit.
Return a concise implementation report with the exact files you successfully wrote, suggested
local checks, and any limitations. Do not claim tests passed when they have not been run.
'''

PROJECT_FILES = {
    'feature.py': '''"""The feature builder's executable flow. The same saved version plans and codes.

Customize the focus strings or orchestration here; edit prompts in planner.md/coder.md.
Host tools return JSON strings. Only import asyncio and registered tool functions are available.
Approval and file conflict checks are enforced by the host, independently of this script.
"""


async def main():
    phase = await get_feature_phase()
    context = await get_feature_request()
    if phase == "plan":
        plan = await plan_feature(context, "Keep the implementation focused and cover every requirement.")
        await save_feature_plan(plan)
    else:
        result = await implement_feature(context, "Implement the approved plan and add regression tests.")
        await save_implementation(result)


await main()
''',
    'planner.md': PLANNER_PROMPT,
    'coder.md': CODER_PROMPT,
    'feature.json': json.dumps(FeatureAgentSettings().model_dump(), indent=2) + '\n',
}


def validate_project(files: dict[str, str]) -> FeatureAgentSettings:
    for path in PROJECT_FILES:
        if path not in files:
            raise ValueError('Missing feature agent file: ' + path)
        if not files[path].strip():
            raise ValueError(path + ' is empty')
        if len(files[path]) > 20_000:
            raise ValueError(path + ' exceeds 20,000 characters')
    settings = FeatureAgentSettings.model_validate_json(files['feature.json'])
    if len(set(settings.tools)) != len(settings.tools) or set(settings.tools) - set(FILE_TOOLS):
        raise ValueError('feature.json tools must be unique names from: ' + ', '.join(FILE_TOOLS))
    if 'feature_write_file' in settings.tools and 'feature_read_file' not in settings.tools:
        raise ValueError('Enable feature_read_file when enabling file writes so the agent can preserve edits')
    try:
        tree = ast.parse(files['feature.py'])
    except SyntaxError as exc:
        raise ValueError(f'feature.py line {exc.lineno}: {exc.msg}') from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name != 'asyncio' for alias in node.names):
            raise ValueError('Code Mode supports import asyncio; use registered tools for I/O')
        if isinstance(node, ast.ImportFrom):
            raise ValueError('Use import asyncio and the registered tool functions')
    return settings


async def validate_code(files: dict[str, str]) -> dict:
    from temporal_agent_harness.harness import agent
    from agent_dag.feature_builder import feature_flow
    settings = validate_project(files)
    error = await agent.code_mode_type_check(feature_flow(), files['feature.py'])
    if error:
        raise ValueError(error)
    return {'ok': True, 'message': 'Feature flow, prompts, settings, and tool contract are valid.',
            'settings': settings.model_dump()}
