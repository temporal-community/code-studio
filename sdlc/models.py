"""Small, explicit contracts for editable review agents and their artifacts."""
from __future__ import annotations

import ast
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TOOL_NAMES = (
    'get_pull_request', 'get_diff', 'read_file', 'search_repository',
    'get_checks', 'get_test_results', 'get_failure_details', 'review_changes', 'save_review',
)


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='PR reviewer', min_length=1, max_length=80)
    model: Literal['gpt-6-luna', 'gpt-6.1-sol'] = 'gpt-6-luna'
    tools: list[str] = Field(default_factory=lambda: list(TOOL_NAMES), min_length=1, max_length=20)


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(max_length=180)
    severity: Literal['high', 'medium', 'low']
    path: str = Field(max_length=300)
    line: int = Field(ge=1)
    explanation: str = Field(max_length=4000)
    suggestion: str = Field(max_length=4000)


class DraftReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(max_length=6000)
    findings: list[Finding] = Field(max_length=30)


class ReviewExecution(BaseModel):
    """Immutable agent code, instructions, and settings captured when a review run starts."""
    script: str
    instructions: str
    settings: AgentSettings


class Expectation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=120)
    path: str = Field(max_length=300)
    contains: str = Field(min_length=1, max_length=200)


class EvaluationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fixture: str = 'demo-empty-average'
    expected_findings: list[Expectation] = Field(default_factory=list, max_length=30)
    max_findings: int = Field(default=10, ge=0, le=30)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_project(files: dict[str, str]) -> tuple[AgentSettings, EvaluationSettings]:
    for path in ('agent.py', 'instructions.md', 'agent.json', 'evaluations.json'):
        if path not in files:
            raise ValueError(f'Missing project file: {path}')
    settings = AgentSettings.model_validate_json(files['agent.json'])
    evaluations = EvaluationSettings.model_validate_json(files['evaluations.json'])
    unknown = set(settings.tools) - set(TOOL_NAMES)
    if unknown:
        raise ValueError(f'Unknown tools: {", ".join(sorted(unknown))}')
    if not {'review_changes', 'save_review'} <= set(settings.tools):
        raise ValueError('The review agent must enable review_changes and save_review')
    if not files['instructions.md'].strip():
        raise ValueError('instructions.md is empty')
    try:
        tree = ast.parse(files['agent.py'])
    except SyntaxError as exc:
        raise ValueError(f'agent.py line {exc.lineno}: {exc.msg}') from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name != 'asyncio' for alias in node.names):
            raise ValueError('Code Mode supports import asyncio; use the registered tools for I/O')
        if isinstance(node, ast.ImportFrom):
            raise ValueError('Use import asyncio and the registered tool functions')
    return settings, evaluations


def evaluate(review: dict, settings: EvaluationSettings, fixture_id: str | None) -> dict:
    if not fixture_id or fixture_id != settings.fixture:
        return {'status': 'not_configured', 'assertions': []}
    findings = review['findings']
    assertions = []
    for expected in settings.expected_findings:
        passed = any(f['path'] == expected.path and expected.contains.casefold() in
                     (f['title'] + ' ' + f['explanation']).casefold() for f in findings)
        assertions.append({'name': expected.name, 'passed': passed})
    assertions.append({'name': f'At most {settings.max_findings} findings',
                       'passed': len(findings) <= settings.max_findings})
    return {'status': 'passed' if all(a['passed'] for a in assertions) else 'failed', 'assertions': assertions}
