import os

# Read at import time by scflows.worker and scflows.create_app
os.environ.setdefault('CELERY_BROKER', 'memory://')
os.environ.setdefault('CELERY_RESULTS_BACKEND', 'cache+memory://')
os.environ.setdefault('CELERY_TIMEZONE', 'UTC')
os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite://')

import pytest


DATA = os.path.join(os.path.dirname(__file__), 'data', 'smartcitizen-data')


@pytest.fixture
def app(tmp_path):
    ''' App with a migrated SQLite database '''
    from flask_migrate import upgrade

    from scflows import create_app

    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'db.sqlite'}"})
    with app.app_context():
        upgrade()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


USERS = {
    'admin-token': {'id': 1, 'username': 'admin', 'role': 'admin'},
    'researcher-token': {'id': 2, 'username': 'researcher', 'role': 'researcher'},
    'citizen-token': {'id': 3, 'username': 'citizen', 'role': 'citizen'},
}


class MeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


@pytest.fixture
def sc_me(monkeypatch):
    ''' Replaces GET {API_URL}me: tokens in USERS are valid, "down" and "slow" fail, others are rejected '''
    import requests

    import scflows.identity as identity

    calls = []

    def get(url, headers=None, timeout=None):
        token = headers['Authorization'].removeprefix('Bearer ')
        calls.append((url, token))
        if token == 'down':
            raise requests.ConnectionError('unreachable')
        if token == 'slow':
            raise requests.Timeout('timeout')
        if token in USERS:
            return MeResponse(200, USERS[token])
        return MeResponse(401, {'message': 'Invalid OAuth2 Params'})

    identity.cache.clear()
    monkeypatch.setattr(identity.requests, 'get', get)
    yield calls
    identity.cache.clear()


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def served_hardware(source, blueprint='test_air', base='http://localhost'):
    ''' A hardware file as flows serves it: the blueprint by name and its url in flows '''
    served = {'blueprint': blueprint, 'blueprint_url': f'{base}/api/v1/blueprints/{blueprint}.json',
              'blueprints': source.get('blueprints', [blueprint])}
    served.update({key: value for key, value in source.items() if key not in ('blueprint', 'blueprint_url', 'blueprints')})
    return served


class FakeRedis:
    ''' The Redis calls of locks.py and of the sync status, in memory '''
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def exists(self, key):
        return int(key in self.values)

    def eval(self, script, count, key, token):
        if self.values.get(key) == token:
            del self.values[key]


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    ''' No Redis in the tests '''
    import scflows.locks as locks
    fake = FakeRedis()
    monkeypatch.setattr(locks, 'client', lambda: fake)
    return fake
