"""Bounded, read-only queries over the same permitted source files as full reads."""
import fnmatch


def read_range(git, feature, path, start_line=1, line_count=80):
    if type(start_line) is not int or start_line < 1 or type(line_count) is not int or not 1 <= line_count <= 200:
        raise ValueError('Use a positive start_line and 1–200 lines')
    file = git.read(feature, path)
    lines = file['content'].splitlines(keepends=True)
    selected = ''.join(lines[start_line - 1:start_line - 1 + line_count])
    end = min(len(lines), start_line - 1 + line_count)
    return {**file, 'content': selected[:20000], 'start_line': start_line, 'end_line': end,
            'total_lines': len(lines), 'truncated': len(selected) > 20000,
            'partial': start_line != 1 or end < len(lines) or len(selected) > 20000}


def search(git, feature, query, glob='*', limit=30):
    if not isinstance(query, str) or not query or len(query) > 200 or '\n' in query:
        raise ValueError('Search requires 1–200 characters on one line')
    if not isinstance(glob, str) or len(glob) > 200 or glob.startswith('/') or '..' in glob.split('/'):
        raise ValueError('Use a workspace-relative file glob')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Use a match limit of 1–50')
    matches = []; scanned = 0; size = 0
    for path in git.files(feature):
        if not fnmatch.fnmatchcase(path, glob): continue
        if scanned >= 2000 or size >= 20_000_000:
            return {'matches': matches, 'files_scanned': scanned, 'truncated': True}
        try: file = git.read(feature, path)
        except (ValueError, UnicodeError): continue
        scanned += 1; size += len(file['content'])
        for number, line in enumerate(file['content'].splitlines(), 1):
            index = line.find(query)
            if index < 0: continue
            if len(matches) == limit:
                return {'matches': matches, 'files_scanned': scanned, 'truncated': True}
            start = max(0, index - 100)
            matches.append({'path': path, 'line': number, 'text': line[start:start + 400],
                            'hash': file['hash'], 'truncated': start > 0 or len(line) > start + 400})
    return {'matches': matches, 'files_scanned': scanned, 'truncated': False}
