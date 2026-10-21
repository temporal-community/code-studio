"""Starter prompt upgrades preserve custom edits and frozen snapshots."""
import pytest
from cooperative.definitions import STAGES, PROJECT_FILES, files_for
from cooperative.models import validate_project
from workspace.store import WorkspaceStore
from tests.test_feature_project import replace

OLD_PROMPTS = {
    'ideation': 'Clarify the feature request without inventing requirements. Identify users, goals, scope, assumptions, measurable outcomes, and acceptance criteria. Incorporate every teammate contribution. Put only implementation-blocking ambiguity in questions; reasonable choices belong in risks. Keep the brief focused and hand concrete requirements to Planner.\n',
    'planner': 'Create a focused implementation plan from the authoritative feature brief and Ideation handoff. Inspect relevant files. Cover acceptance criteria, affected files, ordered implementation steps, regression tests, compatibility, and risks. Address every teammate contribution. Set questions only for blocking decisions. The team approves your plan before Design and Development can begin.\n',
    'design': 'Turn the approved plan into a concise technical design. Inspect the existing project. Specify interfaces, data structures, error handling, affected files, compatibility, and relevant UX states. Prefer existing conventions and minimal changes. Keep within the approved scope. Hand Development concrete implementation details and tests. Do not write code files. Flag blocking design decisions instead of guessing.\n',
    'development': 'Implement the approved plan and Design handoff with focused, maintainable changes. Read files before editing and preserve collaborator edits. Write regression tests. Use the available check tool and fix failures you introduce. On a repeat visit, address Review findings without expanding scope. Name exactly the files you wrote. Report unavailable checks honestly. Leave all changes uncommitted for the team.\n',
    'review': 'Review the implementation against the approved feature plan. Inspect changed files and tests. Distinguish actual check evidence from model claims. Return specific, actionable findings with paths and line numbers where possible. Use verdict changes_requested for blocking defects or failing tests, blocked for missing essential decisions, and ready when the change can be reviewed for release. Do not write files. Do not invent passing tests or silently overlook missing evidence.\n',
    'deployment': 'Prepare a local release preview after the team approves the review. Inspect the recorded checks and changes. Call create_release_preview to package the saved worktree. Document how to run the project, deployment prerequisites, rollout verification, and rollback steps. This environment produces a local preview only: never claim the application was published, pushed, merged, or deployed to a remote service.\n',
}


def test_old_prompt_seeds_upgrade_once_and_custom_edits_survive(tmp_path):
    for stage, text in OLD_PROMPTS.items():
        (tmp_path / files_for(stage)[1]).write_text(text)
    with_custom = 'planner_instructions.md'
    (tmp_path / with_custom).write_text('Use our custom architecture guidance.')
    store = WorkspaceStore(tmp_path)
    assert store.file(with_custom).text == 'Use our custom architecture guidance.'
    for stage in STAGES:
        if stage != 'planner':
            assert store.file(files_for(stage)[1]).text == PROJECT_FILES[files_for(stage)[1]]
    revision = store.revision
    store.close()
    store = WorkspaceStore(tmp_path)
    assert store.revision == revision
    store.close()


def test_intentionally_restored_old_prompt_is_not_overwritten(tmp_path):
    store = WorkspaceStore(tmp_path)
    frozen = store.agent_project_snapshot('planner')
    replace(store, 'planner_instructions.md', OLD_PROMPTS['planner'])
    assert frozen['files']['planner_instructions.md'] == PROJECT_FILES['planner_instructions.md']
    revision = store.revision
    store.close()
    store = WorkspaceStore(tmp_path)
    assert store.file('planner_instructions.md').text == OLD_PROMPTS['planner']
    assert store.revision == revision
    store.close()


@pytest.mark.parametrize('stage', STAGES)
async def test_new_defaults_validate_as_editable_agent_projects(stage):
    files = {name: PROJECT_FILES[name] for name in files_for(stage)}
    await validate_project(stage, files)
    assert len(files[files_for(stage)[1]]) < 20000
