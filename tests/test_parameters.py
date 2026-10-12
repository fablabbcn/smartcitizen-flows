from types import SimpleNamespace

import pytest

from scflows import db, editing
from scflows.metadata import import_metadata
from scflows.parameters import apply_parameters, sensor_types

from conftest import DATA, auth


def channel(name, function='alphasense_als', **kwargs):
    return SimpleNamespace(name=name, function=function, kwargs=kwargs)


def device(channels, versions=None, sensors=('ADC_48_2', 'SCD30_CO2')):
    return SimpleNamespace(channels=channels, versions=versions or [], required_sensors=list(sensors))


@pytest.fixture
def sets(app):
    editing.save_parameter_set('ASB4_NO2', {'channels': {'NO2': {'lam': 1e8, 'p': 0.01}}})
    editing.save_parameter_set('ASB4_OX', {'channels': {'O3': {'lam': 1e9}}})
    editing.save_parameter_set('SCD30', {'channels': {'CO2_BASELINE': {'lam': 1e9}, 'CO2': {'extra_term': 420}}})
    db.session.commit()


def test_sensor_types():
    assert 'ASB4_NO2' in sensor_types() and 'SCD30' in sensor_types()


def test_alphasense_sets_apply_by_sensor_type(sets):
    # 202... is an ASB4_NO2, 212... an ASA4_NO2 (no set)
    item = device([channel('NO2', alphasense_id='202760040', lam=1000), channel('O3', alphasense_id='204123456'),
                   channel('CO2_BASELINE', 'baseline_als', lam=1e10), channel('CO2', 'poly_ts', extra_term=450)],
                  versions=[{'channels': [{'name': 'NO2', 'function': 'alphasense_als',
                                           'kwargs': {'alphasense_id': '212180226'}}]}])

    assert apply_parameters(item) == ['ASB4_NO2', 'ASB4_OX', 'SCD30']

    assert item.channels[0].kwargs == {'alphasense_id': '202760040', 'lam': 1e8, 'p': 0.01}
    assert item.channels[1].kwargs['lam'] == 1e9
    assert item.channels[2].kwargs['lam'] == 1e9 and item.channels[3].kwargs['extra_term'] == 420
    # Another sensor type in an older version: not changed
    assert item.versions[0]['channels'][0]['kwargs'] == {'alphasense_id': '212180226'}


def test_ndir_set_needs_the_sensor(sets):
    item = device([channel('CO2', 'poly_ts', extra_term=450)], sensors=['SCD4X_CO2'])

    assert apply_parameters(item) == []
    assert item.channels[0].kwargs == {'extra_term': 450}


def test_hardware_parameters_win_and_switch_the_algorithm(sets):
    hardware = SimpleNamespace(parameters={'NO2': {'p': 0.1}, 'O3': {'function': 'alphasense_803_04'}})
    item = device([channel('NO2', alphasense_id='202760040'), channel('O3', alphasense_id='204123456')],
                  versions=[{'channels': [{'name': 'O3', 'function': 'alphasense_als', 'kwargs': {}}]}])

    apply_parameters(item, hardware)

    assert item.channels[0].kwargs == {'alphasense_id': '202760040', 'lam': 1e8, 'p': 0.1}
    assert item.channels[1].function == 'alphasense_803_04' and 'function' not in item.channels[1].kwargs
    assert item.versions[0]['channels'][0]['function'] == 'alphasense_803_04'


# API

@pytest.fixture
def client(app, sc_me):
    import_metadata(DATA)
    return app.test_client()


def test_parameters_api(client):
    body = {'channels': {'NO2': {'lam': 1e8}}, 'description': 'Tuned on 5 devices in Barcelona'}

    assert client.put('/api/v1/parameters/ASB4_NO2', json=body, headers=auth('researcher-token')).status_code == 403
    assert client.put('/api/v1/parameters/ASB4_NO2', json=body, headers=auth('admin-token')).status_code == 201
    response = client.put('/api/v1/parameters/NOT_A_TYPE', json=body, headers=auth('admin-token'))
    assert response.status_code == 422 and 'is not a sensor type' in response.get_json()['errors'][0]

    data = client.get('/api/v1/parameters').get_json()
    assert data['parameters'] == {'ASB4_NO2': body} and 'SCD30' in data['sensor_types']
    assert client.get('/api/v1/parameters/ASB4_NO2/revisions').get_json()[0]['username'] == 'admin'
    assert client.delete('/api/v1/parameters/ASB4_NO2', headers=auth('admin-token')).status_code == 204


def test_hardware_parameters_are_stored_and_served(client):
    hardware = client.get('/api/v1/hardware/SCAS_TEST1').get_json()
    hardware['parameters'] = {'O3': {'function': 'alphasense_803_04'}}

    assert client.put('/api/v1/hardware/SCAS_TEST1', json=hardware, headers=auth('admin-token')).status_code == 200
    assert client.get('/api/v1/hardware/SCAS_TEST1').get_json()['parameters'] == hardware['parameters']
