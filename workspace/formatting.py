"""Format a source snapshot without executing it or touching the shared file."""

from difflib import SequenceMatcher
import subprocess
import sys


def format_python(source: str) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'ruff', 'format', '--isolated', '--no-cache',
             '--target-version', 'py311', '--stdin-filename', 'app.py', '-'],
            input=source, capture_output=True, text=True, encoding='utf-8', timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Formatting took too long. Try again with a smaller file.') from exc
    if result.returncode:
        if 'No module named ruff' in result.stderr:
            raise ValueError('The formatter is not installed. Run uv sync and restart just dev.')
        raise ValueError(result.stderr.strip()[:2000] or 'Python formatting failed.')
    formatted = result.stdout
    if formatted == source:
        return {'text': source, 'changes': []}
    # Keep unchanged character IDs, including comments and cursor anchors. Positions
    # are Unicode code points, matching Python strings and the browser's Array.from.
    changes = [
        {'start': i, 'end': j, 'text': formatted[a:b]}
        for tag, i, j, a, b in SequenceMatcher(None, source, formatted, autojunk=False).get_opcodes()
        if tag != 'equal'
    ]
    return {'text': formatted, 'changes': changes}
