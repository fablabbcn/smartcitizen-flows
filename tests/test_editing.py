import copy
import json
from os.path import join

import pytest

from scflows.metadata import import_metadata

from conftest import DATA, auth, served_hardware

with open(join(DATA, 'hardware', 'SCAS_TEST1.json')) as file:
    HARDWARE = json.load(file)

ALPHASENSE = {'ae_electronic_zero_mv': '', 'ae_sensor_zero_mv': -16.64, 'pcb_gain_mv_na': 0.8,
              'we_cross_sensitivity_no2_mv_ppb': '0', 'we_cross_sensitivity_no2_na_ppb': '0',
              'we_electronic_zero_mv': '', 'we_sensitivity_mv_ppb': 0.45464, 'we_sensitivity_na_ppb': 0.5683,
              'we_sensor_zero_mv': -27.2}


@pytest.fixture
def client(app, sc_me):
    import_metadata(DATA)
    return app.test_client()


@pytest.mark.parametrize('method, path', [
    ('put', '/api/v1/hardware/SCAS_NEW'),
    ('put', '/api/v1/blueprints/new'),
    ('put', '/api/v1/calibrations/212999999'),
    ('delete', '/api/v1/hardware/SCAS_TEST1'),
])
def test_writes_require_a_token(client, method, path):
    response = getattr(client, method)(path, json={})

    assert response.status_code == 401
    assert response.get_json()['error'] == 'Unauthorized'


def test_citizens_cannot_write(client):
    response = client.put('/api/v1/calibrations/212999999', json=ALPHASENSE, headers=auth('citizen-token'))

    assert response.status_code == 403


def test_researcher_creates_and_updates_calibration(client):
    response = client.put('/api/v1/calibrations/212999999', json=ALPHASENSE, headers=auth('researcher-token'))
    assert response.status_code == 201
    assert response.get_json() == {'data': ALPHASENSE, 'warnings': []}

    changed = dict(ALPHASENSE, we_sensitivity_na_ppb=0.6)
    response = client.put('/api/v1/calibrations/212999999.json', json=changed, headers=auth('admin-token'))
    assert response.status_code == 200

    assert client.get('/api/v1/calibrations/212999999').get_json() == changed
    revisions = client.get('/api/v1/calibrations/212999999/revisions').get_json()
    assert [(item['action'], item['username']) for item in revisions] == [('update', 'admin'), ('create', 'researcher')]
    assert revisions[0]['before'] == ALPHASENSE and revisions[0]['after'] == changed


def test_invalid_calibration(client):
    response = client.put('/api/v1/calibrations/212999999', json=dict(ALPHASENSE, typo=1),
                          headers=auth('researcher-token'))

    assert response.status_code == 422
    assert response.get_json()['errors'] == ['typo: Extra inputs are not permitted']


def test_put_hardware_with_warnings(client):
    hardware = copy.deepcopy(HARDWARE)
    hardware['description'] = 'Updated'

    response = client.put('/api/v1/hardware/SCAS_TEST1', json=hardware, headers=auth('researcher-token'))

    assert response.status_code == 200
    body = response.get_json()
    assert body['data']['description'] == 'Updated'
    assert body['warnings'] == ['versions.0.ids.AS_48_32: channels not in blueprint test_air: NO2_AE',
                                'versions.0.ids.PT_49_23: channels not in blueprint test_air: ASPT1000, PT1000_POS']
    assert client.get('/api/v1/hardware/SCAS_TEST1.json').get_json() == served_hardware(hardware, 'test_air')


def test_invalid_hardware_is_not_saved(client):
    hardware = copy.deepcopy(HARDWARE)
    hardware['versions'][0]['ids'] = {'AS_48_32': '730002320'}

    response = client.put('/api/v1/hardware/SCAS_TEST1', json=hardware, headers=auth('admin-token'))

    assert response.status_code == 422
    assert response.get_json()['errors'] == ['versions.0.ids.AS_48_32: unknown Alphasense sensor code 730']
    assert client.get('/api/v1/hardware/SCAS_TEST1').get_json() == served_hardware(HARDWARE, 'test_air')


def test_put_hardware_round_trip(client):
    # What flows serves can be sent back as is
    served = client.get('/api/v1/hardware/SCAS_TEST1').get_json()

    response = client.put('/api/v1/hardware/SCAS_TEST1', json=served, headers=auth('admin-token'))

    assert response.status_code == 200
    assert response.get_json()['data'] == served


def test_put_hardware_by_blueprint_name(client):
    hardware = {key: value for key, value in HARDWARE.items() if key != 'blueprint_url'}

    response = client.put('/api/v1/hardware/SCAS_NEW', json=dict(hardware, blueprint='test_air'),
                          headers=auth('admin-token'))
    assert response.status_code == 201
    assert response.get_json()['data']['blueprint_url'] == 'http://localhost/api/v1/blueprints/test_air.json'

    response = client.put('/api/v1/hardware/SCAS_NEW', json=dict(hardware, blueprint='missing'),
                          headers=auth('admin-token'))
    assert response.status_code == 422
    assert response.get_json()['errors'] == ['blueprint: missing is not in flows']


def test_check_hardware_is_public(client):
    response = client.post('/api/v1/hardware/SCAS_NEW/check', json=HARDWARE)

    assert response.status_code == 200
    assert response.get_json()['valid'] is True


def test_hardware_requires_blueprint_in_flows(client):
    hardware = {key: value for key, value in HARDWARE.items() if key != 'blueprint_url'}

    response = client.put('/api/v1/hardware/SCAS_NEW', json=hardware, headers=auth('admin-token'))
    assert response.status_code == 422
    assert response.get_json()['errors'] == ['blueprint: required, the name of a blueprint in flows']

    github = 'https://raw.githubusercontent.com/fablabbcn/smartcitizen-data/master/blueprints/sck_21.json'
    response = client.put('/api/v1/hardware/SCAS_NEW', json=dict(hardware, blueprint_url=github),
                          headers=auth('admin-token'))
    assert response.status_code == 422
    assert response.get_json()['errors'] == ['blueprint: sck_21 is not in flows']


def test_new_blueprint_can_be_used(client):
    blueprint = client.get('/api/v1/blueprints/test_air').get_json()
    assert client.put('/api/v1/blueprints/other', json=blueprint, headers=auth('researcher-token')).status_code == 201

    response = client.put('/api/v1/hardware/SCAS_TEST2', json=dict(HARDWARE, blueprint_url=None, blueprint='other'),
                          headers=auth('researcher-token'))

    assert response.status_code == 200
    assert response.get_json()['data']['blueprint_url'] == 'http://localhost/api/v1/blueprints/other.json'


def test_blueprint_in_use_cannot_be_deleted(client):
    response = client.delete('/api/v1/blueprints/test_air', headers=auth('admin-token'))

    assert response.status_code == 409
    assert response.get_json()['message'] == 'The blueprint is used by hardware: SCAS_TEST1, SCAS_TEST2'
    assert client.get('/api/v1/blueprints/test_air').status_code == 200

    for name in ['SCAS_TEST1', 'SCAS_TEST2']:
        client.delete(f'/api/v1/hardware/{name}', headers=auth('admin-token'))
    assert client.delete('/api/v1/blueprints/test_air', headers=auth('admin-token')).status_code == 204


def test_invalid_blueprint(client):
    response = client.put('/api/v1/blueprints/bad', json={'channels': [{'name': 'NO2'}]},
                          headers=auth('admin-token'))

    assert response.status_code == 422
    assert response.get_json()['errors'] == ['channels.0.function: Field required']


def test_delete_requires_admin(client):
    assert client.delete('/api/v1/hardware/SCAS_TEST1', headers=auth('researcher-token')).status_code == 403

    assert client.delete('/api/v1/hardware/SCAS_TEST1', headers=auth('admin-token')).status_code == 204
    assert client.get('/api/v1/hardware/SCAS_TEST1').status_code == 404
    assert client.delete('/api/v1/hardware/SCAS_TEST1', headers=auth('admin-token')).status_code == 404

    revisions = client.get('/api/v1/hardware/SCAS_TEST1/revisions').get_json()
    assert [item['action'] for item in revisions] == ['delete', 'import']
    assert revisions[0]['before'] == {key: value for key, value in served_hardware(HARDWARE).items()
                                      if key != 'blueprint_url'}
    assert revisions[0]['after'] is None


@pytest.mark.parametrize('path', ['/api/v1/hardware/bad%20name', '/api/v1/calibrations/a.b'])
def test_invalid_names(client, path):
    assert client.put(path, json={}, headers=auth('admin-token')).status_code == 400


def test_body_must_be_an_object(client):
    response = client.put('/api/v1/calibrations/1', data='[1]', content_type='application/json',
                          headers=auth('admin-token'))

    assert response.status_code == 400
