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


def test_refresh_metadata_at_most_every_max_age(monkeypatch):
    import time

    import scflows.tools as tools
    from scdata._config import config as scdata_config

    calls = []
    now = [1000.0]
    monkeypatch.setattr(scdata_config, 'get_meta_data', lambda: calls.append(now[0]))
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(tools, '_metadata_refreshed_at', None)

    assert tools.refresh_metadata(max_age=300) is True
    now[0] += 100
    assert tools.refresh_metadata(max_age=300) is False
    now[0] += 300
    assert tools.refresh_metadata(max_age=300) is True

    assert calls == [1000.0, 1400.0]
