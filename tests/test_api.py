import json
from os.path import join

import pytest

from scflows.metadata import import_metadata

from conftest import DATA


def source_json(*path):
    with open(join(DATA, *path)) as file:
        return json.load(file)


@pytest.fixture
def client(app):
    import_metadata(DATA)
    return app.test_client()


def test_health(client):
    assert client.get('/api/v1/health').get_json() == {'status': 'ok'}


@pytest.mark.parametrize('path', ['SCAS_TEST1', 'SCAS_TEST1.json'])
def test_hardware(client, path):
    response = client.get(f'/api/v1/hardware/{path}')

    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'public, max-age=300'
    assert response.get_json() == source_json('hardware', 'SCAS_TEST1.json')


def test_hardware_optional_keys(client):
    # No comment in the file: no comment key served
    assert client.get('/api/v1/hardware/SCAS_TEST2.json').get_json() == source_json('hardware', 'SCAS_TEST2.json')


def test_hardware_list(client):
    assert client.get('/api/v1/hardware').get_json() == [
        {'name': 'SCAS_TEST1', 'description': '1SEN55-2ELEC-AFE', 'blueprint': 'test_air',
         'url': 'http://localhost/api/v1/hardware/SCAS_TEST1.json'},
        {'name': 'SCAS_TEST2', 'description': 'Forwarded kit', 'blueprint': None,
         'url': 'http://localhost/api/v1/hardware/SCAS_TEST2.json'},
    ]


def test_blueprint(client):
    assert client.get('/api/v1/blueprints/test_air.json').get_json() == source_json('blueprints', 'test_air.json')
    assert client.get('/api/v1/blueprints').get_json() == [
        {'name': 'test_air', 'url': 'http://localhost/api/v1/blueprints/test_air.json'}]


def test_blueprint_keeps_key_order(client):
    assert list(client.get('/api/v1/blueprints/test_air').get_json()) == ['meta', 'channels', 'checks', 'exports']


@pytest.mark.parametrize('path', ['/api/v1/calibrations', '/api/v1/calibrations/calibrations.json'])
def test_calibrations(client, path):
    assert client.get(path).get_json() == source_json('calibrations', 'calibrations.json')


def test_calibrations_by_kind(client):
    assert list(client.get('/api/v1/calibrations?kind=afe_board').get_json()) == ['10-002911']


def test_calibration(client):
    assert client.get('/api/v1/calibrations/10-002911').get_json() == {'t20': 20, 'v20': '0.297'}


@pytest.mark.parametrize('path', ['/api/v1/hardware/NOPE.json', '/api/v1/blueprints/nope', '/api/v1/calibrations/0'])
def test_not_found(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.get_json()['error'] == 'Not Found'
    assert 'Cache-Control' not in response.headers


def test_health_when_the_database_fails(client, monkeypatch):
    from scflows import db

    def fail(*args, **kwargs):
        raise RuntimeError('connection refused to db-host:5432')
    monkeypatch.setattr(db.session, 'execute', fail)

    response = client.get('/api/v1/health')

    assert response.status_code == 503
    assert response.get_json() == {'status': 'unhealthy'}
