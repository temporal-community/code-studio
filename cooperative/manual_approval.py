"""Human overrides retain actual check results and apply only to accepted evidence."""
from sdlc.models import digest
import re


def check_signature(result):
    return digest({key: result.get(key) for key in
        ('status', 'source_hash', 'cache_key', 'exit_code', 'output', 'command')})


def accepted_check(pipeline, name, result, source_hash, revision):
    approval = pipeline['approvals'].get('review', {})
    condition = approval.get('accepted_conditions', {}).get(name)
    return (approval.get('manual') and approval.get('source_hash') == source_hash
            and approval.get('revision') == revision
            and (condition == check_condition(result) if condition is not None else
                 approval.get('accepted_checks', {}).get(name) == check_signature(result)))


def check_condition(result):
    """Only equivalent timeouts tolerate progress/log noise, never new failures."""
    if result.get('status') != 'timed_out' or not result.get('cache_key'):
        return None
    if re.search(r'\b(?:FAILED|ERROR|FAILURES)\b|\b[1-9]\d* (?:failed|errors?)\b', result.get('output', '')):
        return None
    return digest({key: result.get(key) for key in
                   ('status', 'source_hash', 'cache_key', 'command', 'timeout_seconds')})
