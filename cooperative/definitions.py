"""One catalog drives Studio files, workflow stages, tools, and host permissions."""
import json

from .prompts import INSTRUCTIONS

STAGES = ('ideation', 'planner', 'design', 'development', 'review', 'deployment')
READ_TOOLS = ['stage_list_files', 'stage_read_file']
WRITE_TOOLS = [*READ_TOOLS, 'stage_write_file', 'stage_run_check']

DEFINITIONS = {
    'ideation': {
        'name': 'Ideation', 'verb': 'Clarify the idea', 'description': 'Clarify the problem, intended users, outcomes, and feature scope.',
        'input': 'Feature request and teammate contributions', 'output': 'A focused brief with goals and success criteria',
        'tools': READ_TOOLS,
        'instructions': INSTRUCTIONS['ideation'].rstrip(),
    },
    'planner': {
        'name': 'Planner', 'verb': 'Plan the work', 'description': 'Turn the shared brief and repository context into an actionable implementation plan.',
        'input': 'Agreed brief and codebase context', 'output': 'Implementation steps, acceptance criteria, and a test plan',
        'tools': READ_TOOLS,
        'instructions': INSTRUCTIONS['planner'].rstrip(),
    },
    'design': {
        'name': 'Design', 'verb': 'Design the solution', 'description': 'Resolve interfaces, data flow, architecture, and user experience before implementation.',
        'input': 'Approved implementation plan and existing code', 'output': 'A technical design and a precise development handoff',
        'tools': READ_TOOLS,
        'instructions': INSTRUCTIONS['design'].rstrip(),
    },
    'development': {
        'name': 'Development', 'verb': 'Implement and check', 'description': 'Implement the approved design, write regression tests, and run available local checks.',
        'input': 'Approved plan, design, and review feedback', 'output': 'Local code changes, tests, and check evidence',
        'tools': WRITE_TOOLS,
        'instructions': INSTRUCTIONS['development'].rstrip(),
    },
    'review': {
        'name': 'Review', 'verb': 'Review the changes', 'description': 'Review the local diff, test evidence, and acceptance criteria before release.',
        'input': 'Implementation, current diff, and recorded checks', 'output': 'Review findings and a release recommendation',
        'tools': [*READ_TOOLS, 'stage_run_check'],
        'instructions': INSTRUCTIONS['review'].rstrip(),
    },
    'deployment': {
        'name': 'Deployment', 'verb': 'Prepare the release', 'description': 'Prepare a local release preview, rollout instructions, and rollback guidance after approval.',
        'input': 'Approved review, saved code changes, and check evidence', 'output': 'A downloadable local release preview and deployment notes',
        'tools': [*READ_TOOLS, 'stage_run_check', 'create_release_preview'],
        'instructions': INSTRUCTIONS['deployment'].rstrip(),
    },
}


def files_for(stage):
    return [stage + '_agent.py', stage + '_instructions.md', stage + '_settings.json']


def templates():
    files = {}
    for stage, definition in DEFINITIONS.items():
        script, prompt, settings = files_for(stage)
        files[script] = f'''"""{definition['name']} agent: editable orchestration for this stage.
Tools return JSON strings. Use registered host tools; import asyncio is supported.
The host enforces stage permissions, feature approval, and file conflict checks.
"""


async def main():
    context = await get_stage_context()
    result = await run_stage(context, "{definition['verb']}. Keep the handoff clear and actionable.")
    await save_stage_result(result)


await main()
'''
        files[prompt] = definition['instructions'] + '\n'
        files[settings] = json.dumps({'name': definition['name'], 'model': 'gpt-6-luna', 'tools': definition['tools'], 'max_turns': 30 if stage == 'development' else 24 if stage == 'planner' else 12, 'tool_mode': 'code'}, indent=2) + '\n'
    return files


PROJECT_FILES = templates()

# Original instruction seeds; custom Studio playbooks are never auto-replaced.
LEGACY_PROMPT_HASHES = {
    'ideation': '5e4a15d8280ac6113ab39ff4e938d29d1128d808ce27daac0a2b2309e2df9fed',
    'planner': 'fd1d14b8887ac8df2669c314b36f93ec88cd3e10f9ae6dada3d3c1da7ce4edb0',
    'design': 'e7e9fd1f99683b440cacfcc84289ba299bb9cfa4a44eb138af20241460da5056',
    'development': '229849acd656456f67dc1cf996b0e6010208b81d20af73ec0fbe0b1d01d16380',
    'review': '86506c0d11ac34c7337dea4abf616bf8db90949be89e0b3cfec3fc41b338ee8e',
    'deployment': '8e0a47ccf5241f58887fbe3a0b5e38d747a1da0bc9e8b40f23ff966af4bff245',
}

# Previous default UI playbooks, before adopting project-specific conventions.
# Keep hashes rather than bundling obsolete prompt text.
PREVIOUS_UI_PROMPT_HASHES = {
    'design': 'b2a3a494c1fcdc243775400b52eedf659e459812b7fb18efc7ccbd234bf25021',
    'development': 'da592871790f1c1985a550dfc4165846ce2711245ae6066b7b244ef525da828a',
    'review': 'bd1e320be412968b1b05f9b86620a15df5d0c61c43e1061acb3f2d955570bce1',
}
