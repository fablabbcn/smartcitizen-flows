import pytest

import scflows.identity as identity

from conftest import MeResponse

PASSWORDS = {'admin': 'admin-token', 'researcher': 'researcher-token', 'citizen': 'citizen-token'}


@pytest.fixture
def sc_sessions(monkeypatch, sc_me):
    ''' Replaces POST {API_URL}sessions: password "secret" for the users in PASSWORDS '''
    import requests

    def post(url, json=None, timeout=None):
        if json['username'] == 'down':
            raise requests.ConnectionError('unreachable')
        if json['username'] in PASSWORDS and json['password'] == 'secret':
            return MeResponse(200, {'access_token': PASSWORDS[json['username']]})
        return MeResponse(422, {'password': 'is incorrect'})

    monkeypatch.setattr(identity.requests, 'post', post)


def login(client, name='admin', password='secret'):
    return client.post('/login', data={'name': name, 'password': password})


def test_index(client):
    assert client.get('/').status_code == 200


def test_jobs_require_login(client):
    response = client.get('/jobs/')

    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_admin_signs_in(client, sc_sessions):
    response = login(client)

    assert '/jobs/' in response.headers['Location']
    assert client.get('/jobs/').status_code == 200
    page = client.get('/').get_data(as_text=True)
    assert 'Log out' in page and 'admin' in page
    assert 'href="/jobs/"' in page


def test_researcher_cannot_see_jobs(client, sc_sessions):
    assert login(client, 'researcher').headers['Location'] == '/'

    assert client.get('/jobs/').status_code == 403
    assert 'href="/jobs/"' not in client.get('/').get_data(as_text=True)


def test_citizen_cannot_sign_in(client, sc_sessions):
    response = login(client, 'citizen')

    assert '/login' in response.headers['Location']
    assert client.get('/jobs/').status_code == 302
    assert 'Only Smart Citizen admins and researchers' in client.get('/login').get_data(as_text=True)


def test_wrong_password(client, sc_sessions):
    assert '/login' in login(client, password='wrong').headers['Location']
    assert client.get('/jobs/').status_code == 302


def test_api_unreachable(client, sc_sessions):
    assert login(client, 'down').status_code == 503


def test_session_keeps_identity_not_token(app, client, sc_sessions):
    login(client)

    with client.session_transaction() as session:
        assert session['identity'] == {'id': 1, 'username': 'admin', 'role': 'admin', 'hardware': []}
        assert 'admin-token' not in str(dict(session))
        assert session.permanent


def test_logout(client, sc_sessions):
    login(client)

    assert client.get('/logout').status_code == 302
    assert client.get('/jobs/').status_code == 302


def test_secure_cookie_with_https_public_url(monkeypatch, tmp_path):
    from scflows import create_app

    monkeypatch.setenv('PUBLIC_URL', 'https://flows.smartcitizen.me')
    app = create_app({'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'db.sqlite'}"})

    assert app.config['SESSION_COOKIE_SECURE'] is True
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_error_pages(client, sc_sessions):
    page = client.get('/nothing')
    assert page.status_code == 404 and 'This page does not exist' in page.get_data(as_text=True)

    login(client, 'researcher')
    page = client.get('/jobs/')
    assert page.status_code == 403 and 'admins only' in page.get_data(as_text=True)


def test_api_errors_stay_json(client):
    response = client.get('/api/v1/nothing')

    assert response.status_code == 404
    assert response.get_json()['error'] == 'Not Found'


def test_theme_switch(client):
    page = client.get('/').get_data(as_text=True)

    assert 'data-theme-switch' in page and "localStorage.getItem('theme')" in page
