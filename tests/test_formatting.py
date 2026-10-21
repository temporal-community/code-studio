import ast

from fastapi.testclient import TestClient

from workspace.formatting import format_python
from workspace.server import create_app


def test_formatter_preserves_python_meaning_comments_and_unicode():
    source = '# Keep 🧪 and <tags>\ndef add( a:int,b:int )->int:\n return a+b\n'
    result = format_python(source)
    assert result['text'] == '# Keep 🧪 and <tags>\ndef add(a: int, b: int) -> int:\n    return a + b\n'
    assert ast.dump(ast.parse(result['text'])) == ast.dump(ast.parse(source))
    replay = source
    for change in reversed(result['changes']):
        replay = replay[:change['start']] + change['text'] + replay[change['end']:]
    assert replay == result['text']
    assert format_python(result['text'])['changes'] == []


def test_format_endpoint_does_not_execute_source_or_modify_shared_file(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        before = (tmp_path / 'app.py').read_text()
        result = client.post('/workspace/format', json={'source': 'raise RuntimeError( "must not execute" )'})
        assert result.status_code == 200
        assert result.json()['text'] == 'raise RuntimeError("must not execute")\n'
        assert (tmp_path / 'app.py').read_text() == before
        assert client.get('/workspace/health').json()['revision'] == 0


def test_invalid_python_reports_a_parse_error_and_preserves_file(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        before = (tmp_path / 'app.py').read_text()
        response = client.post('/workspace/format', json={'source': 'def broken(:\n'})
        assert response.status_code == 422
        assert 'parse' in response.json()['detail'].lower()
        assert (tmp_path / 'app.py').read_text() == before


def test_formatter_rejects_oversize_source(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        response = client.post('/workspace/format', json={'source': '#' + 'x' * 20_000})
        assert response.status_code == 422
