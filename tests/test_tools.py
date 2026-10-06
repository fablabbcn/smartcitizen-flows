import os

from scflows.tools import LazyCallable, load_env, parse_sc_json, url_checker


def test_parse_sc_json():
    assert parse_sc_json('{blueprint_url:https://example.com/a.json,latest_postprocessing:2020-10-29T08:35:23Z}') == {
        'blueprint_url': 'https://example.com/a.json',
        'latest_postprocessing': '2020-10-29T08:35:23Z',
    }


def test_url_checker():
    assert url_checker('see https://example.com/a.json') == ['https://example.com/a.json']
    assert url_checker('SCAS220013') == []
    assert url_checker(None) == []


def test_lazy_callable():
    join = LazyCallable('os.path.join')

    assert join('a', 'b') == 'a/b'


def test_load_env(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('# comment\n\nSCFLOWS_TEST_KEY=value=with=equals\n')

    assert load_env(str(env)) is True
    assert os.environ.pop('SCFLOWS_TEST_KEY') == 'value=with=equals'
    assert load_env(str(tmp_path / 'missing')) is False
