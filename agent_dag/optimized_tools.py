"""Additional typed capabilities selected only by newly frozen run inputs."""
from pydantic import BaseModel
from temporal_agent_harness.harness import agent
from .stage_tools import FileRead, FileWrite, CheckResult


class FileRange(FileRead):
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool
    partial: bool


class SourceMatch(BaseModel):
    path: str
    line: int
    text: str
    hash: str
    truncated: bool


class SourceSearch(BaseModel):
    matches: list[SourceMatch]
    files_scanned: int
    truncated: bool


@agent.callback_tool_defn(inherently_safe=True)
async def stage_read_range(path: str, start_line: int = 1, line_count: int = 80) -> FileRange:
    """Read up to 200 source lines. Partial content cannot replace a complete file;
    use stage_read_file before overwriting an existing file."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def stage_search_source(query: str, glob: str = '*', limit: int = 30) -> SourceSearch:
    """Find literal, case-sensitive text in permitted source files, with line numbers.
    A truncated result is incomplete; narrow the glob or query."""
    ...


READ_TOOLS = [stage_read_range, stage_search_source]


@agent.callback_tool_defn(inherently_safe=True)
async def stage_patch_file(path: str, expected_hash: str, old_text: str, new_text: str) -> FileWrite:
    """Replace one exact, unique block in an existing file. Each block is at most 20 KB.
    Use the full-file hash returned by a source read or the previous successful edit.
    old_text must be nonempty and match exactly once, including whitespace and line endings.
    Include surrounding context if ambiguous. Empty new_text removes the matched block.
    Stale hashes and missing/ambiguous matches fail without editing. Reread before retrying.
    Await edits sequentially. Use stage_write_file for new files or full replacements."""
    ...


PATCH_GUIDANCE = '''\nPrefer stage_patch_file for small edits to existing files. Use source range reads
to capture the exact old_text and the full-file hash; include enough context to match
once. Preserve whitespace and line endings. The host preserves all unmatched content.
Await patches sequentially, using the returned hash for the next edit to that file.
After a conflict or missing/ambiguous match, reread and adjust the patch. Never guess a
hash or retry an entire script after partial success. Use stage_write_file for new
files or intentional full replacements, reading the full existing file first.'''


def patch_enabled(settings, context):
    return context.get('source_patch_version') == 1 and {'stage_read_file', 'stage_write_file'} <= set(settings.tools)


class ValidationResult(CheckResult):
    scope: str = 'full'
    targets: list[str] = []
    cached: bool = False


@agent.callback_tool_defn(inherently_safe=True)
async def stage_run_check(check: str, targets: list[str], force: bool) -> ValidationResult:
    """Run or reuse validation. Use targets=[] for full coverage, or Python test
    paths for focused python_tests (Python source paths for syntax). Other checks
    require targets=[]. Use force=False normally; force=True retries unchanged
    inputs for flaky tests or external-state changes. Cached failures remain failures.
    Focused passes never satisfy final full-project validation."""
    ...
