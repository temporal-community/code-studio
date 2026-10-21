"""Versioned feature inputs and structured planning/implementation artifacts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FeatureCreate(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(default='', max_length=8000)
    author: str = Field(min_length=1, max_length=40)
    repository: str = Field(min_length=1, max_length=1000)
    base: str = Field(default='HEAD', min_length=1, max_length=200)
    branch: str = Field(min_length=1, max_length=160)


class FeatureEdit(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(max_length=8000)
    author: str = Field(min_length=1, max_length=40)


class Contribution(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    author: str = Field(min_length=1, max_length=40)
    kind: Literal['requirement', 'question', 'decision', 'feedback'] = 'feedback'
    text: str = Field(min_length=1, max_length=4000)


class FeatureAction(StrictModel):
    agent_version: str | None = Field(default=None, max_length=64)
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    revision: int = Field(ge=1)
    author: str = Field(min_length=1, max_length=40)


class PlanStep(StrictModel):
    title: str = Field(max_length=200)
    details: str = Field(max_length=2000)
    files: list[str] = Field(max_length=20)


class InputCoverage(StrictModel):
    input_id: str
    handling: str = Field(max_length=1500)


class FeaturePlan(StrictModel):
    summary: str = Field(max_length=6000)
    goals: list[str] = Field(max_length=20)
    acceptance_criteria: list[str] = Field(max_length=30)
    out_of_scope: list[str] = Field(max_length=20)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    test_plan: list[str] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(max_length=20)
    open_questions: list[str] = Field(max_length=15)
    input_coverage: list[InputCoverage] = Field(max_length=100)


class FeatureExecution(StrictModel):
    """Frozen feature brief, collaborator contributions, and local repository context."""
    mode: Literal['plan', 'code']
    brief: dict
    context: dict
    plan: FeaturePlan | None = None
    agent_project: dict | None = None


class ImplementationResult(StrictModel):
    summary: str = Field(max_length=6000)
    changed_files: list[str] = Field(max_length=40)
    suggested_checks: list[str] = Field(max_length=20)
    limitations: list[str] = Field(max_length=20)
