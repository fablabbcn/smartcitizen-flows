from types import SimpleNamespace

import pytest

import scflows.auth as auth_module
from scflows import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('SQLALCHEMY_DATABASE_URI', f"sqlite:///{tmp_path / 'db.sqlite'}")
    app = create_app()
    app.config['TESTING'] = True
    return app.test_client()


@pytest.fixture
def sc_api(monkeypatch):
    ''' Replaces the SC API session and user requests '''
    def respond(status_code=200, role='admin'):
        monkeypatch.setattr(auth_module.requests, 'post',
                            lambda **kwargs: SimpleNamespace(status_code=status_code))
        monkeypatch.setattr(auth_module.requests, 'get',
                            lambda **kwargs: SimpleNamespace(json=lambda: {'role': role}))
    return respond


def signup(client, name='admin', password='secret'):
    return client.post('/signup', data={'name': name, 'password': password})


def login(client, name='admin', password='secret'):
    return client.post('/login', data={'name': name, 'password': password})


def test_index(client):
    assert client.get('/').status_code == 200


def test_tasks_require_login(client):
    response = client.get('/tasks')

    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_signup_and_login_admin(client, sc_api):
    sc_api(role='admin')

    assert '/login' in signup(client).headers['Location']
    assert '/tasks' in login(client).headers['Location']


def test_signup_rejects_non_admin(client, sc_api):
    sc_api(role='researcher')

    assert '/signup' in signup(client).headers['Location']
    assert '/login' in login(client).headers['Location']


def test_signup_rejects_invalid_credentials(client, sc_api):
    sc_api(status_code=422)

    assert '/signup' in signup(client).headers['Location']


def test_login_with_wrong_password(client, sc_api):
    sc_api(role='admin')
    signup(client)

    assert '/login' in login(client, password='wrong').headers['Location']
