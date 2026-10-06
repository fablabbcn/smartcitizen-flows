import os

# Read at import time by scflows.worker and scflows.create_app
os.environ.setdefault('CELERY_BROKER', 'memory://')
os.environ.setdefault('CELERY_RESULTS_BACKEND', 'cache+memory://')
os.environ.setdefault('CELERY_TIMEZONE', 'UTC')
os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite://')

import pytest
from crontab import CronTab


@pytest.fixture(autouse=True)
def no_user_crontab(monkeypatch):
    ''' Never write the crontab of the user running the tests '''
    monkeypatch.setattr(CronTab, 'write_to_user', lambda self, user=True: None)


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
    ''' Replaces GET {API_URL}me: tokens in USERS are valid, "down" fails, others are rejected '''
    import requests

    import scflows.identity as identity

    calls = []

    def get(url, headers=None, timeout=None):
        token = headers['Authorization'].removeprefix('Bearer ')
        calls.append((url, token))
        if token == 'down':
            raise requests.ConnectionError('unreachable')
        if token in USERS:
            return MeResponse(200, USERS[token])
        return MeResponse(401, {'message': 'Invalid OAuth2 Params'})

    identity.cache.clear()
    monkeypatch.setattr(identity.requests, 'get', get)
    yield calls
    identity.cache.clear()


def auth(token):
    return {'Authorization': f'Bearer {token}'}
