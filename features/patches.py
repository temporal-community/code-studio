"""Exact single-block replacements; no fuzzy matching or line-ending normalization."""
import re

from .git import MAX_FILE


def validate_patch(expected_hash, old_text, new_text):
    if not isinstance(expected_hash, str) or re.fullmatch(r'[0-9a-f]{64}', expected_hash) is None:
        raise ValueError('Patches require the full file hash from a current source read.')
    if not isinstance(old_text, str) or not old_text:
        raise ValueError('old_text must be a nonempty exact block. Use stage_write_file to create a file.')
    if not isinstance(new_text, str):
        raise ValueError('new_text must be text; use an empty string to remove the matched block.')
    if any('\x00' in text or len(text.encode('utf-8')) > 20000 for text in (old_text, new_text)):
        raise ValueError('Each patch block must be UTF-8 text no larger than 20 KB, without null bytes.')
    if old_text == new_text:
        raise ValueError('The patch makes no change. Supply a different replacement.')


def patched_content(file, expected_hash, old_text, new_text):
    validate_patch(expected_hash, old_text, new_text)
    if file['hash'] != expected_hash:
        raise ValueError('File changed since it was read. Reread and preserve the collaborator’s changes.')
    content = file['content']
    start = content.find(old_text)
    if start < 0:
        raise ValueError('old_text was not found exactly. Reread the relevant range and preserve whitespace.')
    if start != content.rfind(old_text):
        raise ValueError('old_text matches more than once. Include surrounding context to identify one block.')
    updated = content[:start] + new_text + content[start + len(old_text):]
    if len(updated.encode('utf-8')) > MAX_FILE:
        raise ValueError('The patched file exceeds the 100 KB source-file limit.')
    return updated
