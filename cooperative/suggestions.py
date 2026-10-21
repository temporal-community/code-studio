"""Bounded, read-only project evidence for fresh Code Studio feature suggestions."""
import asyncio
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints
from features.models import StrictModel
from features.git import LocalGit, safe_path
from .source import source_paths

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class SuggestionSourceError(ValueError):
    pass


class SuggestionRequest(StrictModel):
    author: str = Field(min_length=1, max_length=40)
    previous_titles: list[Title] = Field(default_factory=list, max_length=10)
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ''
    repository: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None
    source_mode: Literal['current', 'git'] = 'current'
    base: str = Field(default='HEAD', min_length=1, max_length=200)


class FeatureSuggestion(StrictModel):
    title: Title
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)]
    acceptance: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class SuggestionInput(StrictModel):
    model: Literal['gpt-6-luna', 'gpt-6.1-sol']
    context: dict
    instructions: str | None = Field(default=None, min_length=1, max_length=20000)
    instructions_version: str | None = None


def project_evidence(root: Path, source_mode='current', base='HEAD'):
    if not root.is_dir():
        raise ValueError('The working directory does not exist')
    revision = None
    if source_mode == 'git':
        git = LocalGit(root)
        revision = git.git(root, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}')
        paths = []
        for entry in git.git(root, 'ls-tree', '-rz', '--full-tree', revision).split('\0'):
            if not entry:
                continue
            metadata, name = entry.split('\t', 1)
            mode, kind, _ = metadata.split()
            if kind != 'blob' or mode == '120000':
                continue
            try:
                safe_path(name)
            except ValueError:
                continue
            paths.append(name)
    else:
        paths = source_paths(root)
    preferred = ['README.md', 'ui/Work.svelte', 'ui/Workspace.svelte', 'ui/Agents.svelte',
                 'cooperative/api.py', 'cooperative/prompts.py', 'cooperative/runtime.py',
                 'package.json', 'pyproject.toml']
    excerpts = []
    budget = 80_000
    for name in preferred:
        if name not in paths or budget <= 0:
            continue
        try:
            if revision:
                if int(git.git(root, 'cat-file', '-s', revision + ':' + name)) > 2_000_000:
                    continue
                content = git.git(root, 'show', revision + ':' + name)
            else:
                path = root / name
                # Recheck after enumeration; never follow links outside the project.
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    continue
                content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            continue
        excerpt = content[:min(16_000, budget)]
        excerpts.append({'path': name, 'content': excerpt, 'truncated': len(excerpt) < len(content)})
        budget -= len(excerpt)
    return {'paths': paths[:500], 'file_count': len(paths), 'excerpts': excerpts,
            'note': ('Committed source at ' + revision if revision else 'Current local source, including uncommitted edits.')
                    + ' Excerpts may be incomplete.'}


async def suggest(service, root: Path, request: SuggestionRequest):
    from .models import StageSettings
    from .definitions import files_for
    instructions = service.workspace.support_instruction_snapshot('improvement-advisor')
    root = Path(request.repository).expanduser().resolve() if request.repository else root.resolve()
    try:
        evidence = await asyncio.to_thread(project_evidence, root, request.source_mode, request.base)
    except (ValueError, OSError) as exc:
        raise SuggestionSourceError('The selected source could not be read') from exc
    project = service.workspace.agent_project_snapshot('ideation')
    settings = StageSettings.model_validate_json(project['files'][files_for('ideation')[2]])
    existing = [item for item in service.store.list() if Path(item['repository']).resolve() == root.resolve()][:50]
    context = {
        'repository': evidence,
        'requested_feature': request.description,
        'existing_features': [{'title': item['title'], 'status': item['status']} for item in existing],
        'previous_suggestions': request.previous_titles,
        'requested_by': request.author,
    }
    with service.workspace.support_activity.track('improvement-advisor'):
        result = await service.runtime.suggest_improvement(SuggestionInput(model=settings.model, context=context,
            instructions=instructions['content'], instructions_version=instructions['version']))
        return {**FeatureSuggestion.model_validate(result).model_dump(), 'repository': str(root)}
