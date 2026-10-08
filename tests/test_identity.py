from scflows import identity

from conftest import auth


def test_token_is_verified_once(app, client, sc_me):
    for _ in range(3):
        assert client.put('/api/v1/calibrations/1', json={'t20': 20, 'v20': '0.3'},
                          headers=auth('admin-token')).status_code in (200, 201)

    assert sc_me == [('https://api.smartcitizen.me/v0/me', 'admin-token')]


def test_api_url_from_environment(monkeypatch, tmp_path, sc_me):
    from scflows import create_app

    monkeypatch.setenv('API_URL', 'https://staging-api.smartcitizen.me/v0')
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'db.sqlite'}"})
    with app.test_request_context(headers=auth('researcher-token')):
        assert identity.current_identity() == identity.Identity(2, 'researcher', 'researcher')

    assert sc_me[0][0] == 'https://staging-api.smartcitizen.me/v0/me'


def test_invalid_token_is_cached(app, client, sc_me):
    for _ in range(2):
        assert client.put('/api/v1/calibrations/1', json={}, headers=auth('bad')).status_code == 401

    assert len(sc_me) == 1


def test_cache_expires(app, sc_me, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(identity.time, 'monotonic', lambda: now[0])

    identity.verify_token('admin-token')
    now[0] += identity.TOKEN_TTL + 1
    identity.verify_token('admin-token')

    assert len(sc_me) == 2


def test_api_unreachable(client, sc_me):
    response = client.put('/api/v1/calibrations/1', json={}, headers=auth('down'))

    assert response.status_code == 503
    assert 'not reachable' in response.get_json()['message']


def test_bearer_scheme_required(client, sc_me):
    response = client.put('/api/v1/calibrations/1', json={}, headers={'Authorization': 'Token admin-token'})

    assert response.status_code == 401
    assert sc_me == []


def test_api_timeout(client, sc_me):
    response = client.put('/api/v1/calibrations/1', json={}, headers=auth('slow'))

    assert response.status_code == 503
    assert f'did not answer in {identity.ME_TIMEOUT} seconds' in response.get_json()['message']


def test_hardware_name():
    assert identity.hardware_name('SCAS220013') == 'SCAS220013'
    assert identity.hardware_name(
        'https://raw.githubusercontent.com/fablabbcn/smartcitizen-data/master/hardware/SCAS220013.json') == 'SCAS220013'
    assert identity.hardware_name('https://flows.smartcitizen.me/api/v1/hardware/SCAS220013.json') == 'SCAS220013'
    assert identity.hardware_name('https://example.com/blueprints/sc_air.json') is None
    assert identity.hardware_name('not a name') is None
    assert identity.hardware_name(None) is None


def test_researchers_get_the_hardware_of_their_devices(app, monkeypatch):
    devices = [{'id': 1, 'postprocessing': {'hardware_url': 'SCAS2'}},
               {'id': 2, 'postprocessing': {'hardware_url': 'https://flows.smartcitizen.me/api/v1/hardware/SCAS1.json'}},
               {'id': 3, 'postprocessing': None},
               {'id': 4, 'postprocessing': {'hardware_url': 'SCAS2'}}]

    class Response:
        status_code = 200

        def __init__(self, role):
            self.role = role

        def json(self):
            return {'id': 7, 'username': 'someone', 'role': self.role, 'devices': devices}

    identity.cache.clear()
    monkeypatch.setattr(identity.requests, 'get', lambda url, headers, timeout: Response(headers['Authorization'][7:]))

    assert identity.verify_token('researcher').hardware == ('SCAS1', 'SCAS2')
    # Admins see everything: their own devices do not matter
    assert identity.verify_token('admin').hardware == ()
    identity.cache.clear()
