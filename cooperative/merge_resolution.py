"""Structured decisions from the merge agent; only the host can write files."""
from typing import Literal
from pydantic import Field
from features.models import StrictModel


class MergeResolution(StrictModel):
    choice: Literal['local', 'feature', 'edit', 'blocked']
    content: str | None = Field(default=None, max_length=100000)
    summary: str = Field(min_length=1, max_length=2000)


class MergeResolutionInput(StrictModel):
    model: Literal['gpt-6-luna', 'gpt-6.1-sol']
    context: dict
    instructions: str | None = Field(default=None, min_length=1, max_length=20000)
    instructions_version: str | None = None
