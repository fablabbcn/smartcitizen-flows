import json
from os.path import join

import pytest

import scflows.names as names_module
from scflows import db
from scflows.metadata import import_metadata, verify_metadata
from scflows.models import Revision, SensorName
from scflows.names import FirmwareSensor, parse_firmware, plan

from conftest import DATA, auth


@pytest.fixture
def client(app, sc_me):
    import_metadata(DATA)
    return app.test_client()


def served(client):
    return client.get('/api/v1/names/SCDevice.json').get_json()


# Import, serve and verify

def test_names_are_served_in_the_order_of_the_file(client):
    with open(join(DATA, 'names', 'SCDevice.json')) as file:
        source = json.load(file)

    assert served(client) == [dict(item, id=int(item['id'])) for item in source]
    assert client.get('/api/v1/names').get_json() == served(client)
    assert client.get('/api/v1/names/TEMP').get_json() == {'name': 'TEMP', 'id': 55, 'description': 'Temperature',
                                                            'unit': 'C'}
    assert 'names' in client.get('/api/v1/').get_json()['links']


def test_verify_compares_names(client):
    assert verify_metadata(DATA) == []

    db.session.execute(db.update(SensorName).filter_by(name='TEMP').values(unit='K'))
    db.session.commit()

    assert verify_metadata(DATA) == ['names/SCDevice.json: served content differs']


# API

def test_admin_adds_a_name_at_the_end(client):
    response = client.put('/api/v1/names/SCD4X_CO2', json={'id': 258, 'description': 'SCD4X CO2', 'unit': 'ppm'},
                          headers=auth('admin-token'))

    assert response.status_code == 201
    assert served(client)[-1] == {'name': 'SCD4X_CO2', 'id': 258, 'description': 'SCD4X CO2', 'unit': 'ppm'}
    revisions = client.get('/api/v1/names/SCD4X_CO2/revisions').get_json()
    assert [(item['action'], item['username']) for item in revisions] == [('create', 'admin')]


def test_update_keeps_the_position(client):
    before = [item['name'] for item in served(client)]

    response = client.put('/api/v1/names/TEMP', json={'name': 'TEMP', 'id': 55, 'description': 'Air temperature',
                                                      'unit': 'C'}, headers=auth('admin-token'))

    assert response.status_code == 200
    assert [item['name'] for item in served(client)] == before
    assert client.get('/api/v1/names/TEMP').get_json()['description'] == 'Air temperature'


@pytest.mark.parametrize('body, error', [
    ({'id': -1}, 'id: Input should be greater than or equal to 0'),
    ({'id': 1, 'typo': 1}, 'typo: Extra inputs are not permitted'),
    ({'name': 'OTHER', 'id': 1}, 'name: OTHER in the body, TEMP in the path'),
])
def test_invalid_names(client, body, error):
    response = client.put('/api/v1/names/TEMP', json=body, headers=auth('admin-token'))

    assert response.status_code == 422
    assert response.get_json()['errors'] == [error]


def test_only_admins_change_names(client):
    assert client.put('/api/v1/names/NEW', json={'id': 1}, headers=auth('researcher-token')).status_code == 403
    assert client.delete('/api/v1/names/TEMP', headers=auth('researcher-token')).status_code == 403

    assert client.delete('/api/v1/names/TEMP', headers=auth('admin-token')).status_code == 204
    assert client.get('/api/v1/names/TEMP').status_code == 404


# Firmware

FIRMWARE = '''
    OneSensor { BOARD_BASE,     100,    SENSOR_BATT_PERCENT,    "BATT",         "Battery",          10,     true,   1,  "%",    false },
    OneSensor { BOARD_BASE,     100,    SENSOR_BATT_VOLTAGE,    "BATT_VOLT",    "Battery voltage",  222,    false,  1,  "V",    false },
    // OneSensor { BOARD_URBAN, 0, SENSOR_OLD, "OLD", "Commented out", 300, true, 1, "x" },
    OneSensor { BOARD_URBAN,    0,      SENSOR_TEMPERATURE,     "TEMP",         "Temperature",      55,     true,   1,  "C"         },
    OneSensor { BOARD_AUX,      100,    SENSOR_GROVE_OLED,      "GR_OLED",      "Grove OLED",       0,      false,  1           },
'''


def test_parse_firmware():
    assert parse_firmware(FIRMWARE) == [
        FirmwareSensor('BATT', 'Battery', 10, '%'),
        FirmwareSensor('BATT_VOLT', 'Battery voltage', 222, 'V'),
        FirmwareSensor('TEMP', 'Temperature', 55, 'C'),
        FirmwareSensor('GR_OLED', 'Grove OLED', 0, ''),
    ]


# Planning

def name(name, sensor_id, unit=''):
    return {'name': name, 'id': sensor_id, 'description': name.title(), 'unit': unit}


def sensor(name, sensor_id, unit=''):
    return FirmwareSensor(name, name.title(), sensor_id, unit)


def api(*ids):
    return {sensor_id: {'name': f'Sensor {sensor_id}', 'description': '', 'unit': ''} for sensor_id in ids}


def test_plan_sets_missing_ids():
    changes, notices = plan([name('BATT_VOLT', 0, 'V')], api(222), [sensor('BATT_VOLT', 222, 'V')])

    assert [(change.name, change.before['id'], change.after) for change in changes] == [
        ('BATT_VOLT', 0, {'name': 'BATT_VOLT', 'id': 222, 'description': 'Batt_Volt', 'unit': 'V'})]
    assert changes[0].reasons == ['the firmware gives BATT_VOLT the id 222']
    assert notices == []


def test_plan_says_which_name_scdata_would_use():
    changes, _ = plan([name('SD-card', 221), name('SDCARD', 0)], api(221), [sensor('SDCARD', 221)])

    assert changes[0].reasons == ['the firmware gives SDCARD the id 221; SD-card already has it, '
                                  'scdata would use SD-card']


def test_plan_adds_new_firmware_sensors_once():
    firmware = [sensor('SCD4X_CO2', 258, 'ppm'), sensor('SCD4X_CO2', 258, 'ppm'), sensor('NEXT', 300)]

    changes, _ = plan([], api(258), firmware)

    assert [(change.name, change.is_new, change.after['unit']) for change in changes] == [
        ('SCD4X_CO2', True, 'ppm'), ('NEXT', True, '')]
    assert changes[1].reasons == ['new in the firmware: "Next"; not in the Smart Citizen API yet']


def test_plan_never_renames_or_changes_units():
    names = [name('PRESS', 58, 'kPa')]
    firmware = [sensor('MPL_PRESS', 58, 'Pa')]

    changes, notices = plan(names, {58: {'name': 'Pressure', 'description': '', 'unit': 'hPa'}}, firmware,
                            used={'PRESS': ['sc_air']})

    assert changes == []
    assert notices == ['id 58: PRESS (used by sc_air) in flows, MPL_PRESS in the firmware']


def test_plan_reports_what_to_review():
    names = [name('GB_TEMP', 79), name('SHT31_EXT_TEMP', 79), name('GONE', 999), name('BME680_TEMP', 0)]

    _, notices = plan(names, api(79, 3, 4), [], used={'GB_TEMP': ['sc_air']})

    assert notices == [
        'GONE: id 999 is not in the Smart Citizen API',
        '1 names have no id and the firmware has no sensor with that name (renamed in the firmware, '
        'or only on the SD card): BME680_TEMP',
        'id 79 has several names: GB_TEMP (used by sc_air), SHT31_EXT_TEMP. scdata uses GB_TEMP',
        '2 sensors of the Smart Citizen API have no name and are not in the firmware '
        '(older kits, or groups of sensors): 3, 4',
    ]


# Command

@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(names_module, 'api_sensors', lambda: api(10, 55, 79, 133, 14, 222, 258))
    monkeypatch.setattr(names_module, 'read_firmware', lambda location: [
        sensor('BATT_VOLT', 222, 'V'), sensor('SCD4X_CO2', 258, 'ppm'), sensor('TEMP', 55, 'C')])


def sync(app, *args, input=None):
    return app.test_cli_runner().invoke(args=['names', 'sync', *args], input=input)


def test_sync_dry_run_changes_nothing(app, client, sources):
    result = sync(app, '--dry-run')

    assert 'Update BATT_VOLT: id 0 -> 222' in result.output
    assert 'Add SCD4X_CO2: id 258, "Scd4X_Co2", unit "ppm"' in result.output
    assert 'Dry run: nothing changed' in result.output
    assert client.get('/api/v1/names/BATT_VOLT').get_json()['id'] == 0
    assert client.get('/api/v1/names/SCD4X_CO2').status_code == 404


def test_sync_asks_for_each_change(app, client, sources):
    result = sync(app, input='y\nn\n')

    assert '1 of 2 changes applied' in result.output
    db.session.expire_all()
    assert client.get('/api/v1/names/BATT_VOLT').get_json()['id'] == 222
    assert client.get('/api/v1/names/SCD4X_CO2').status_code == 404
    revision = db.session.execute(db.select(Revision).filter_by(kind='name', key='BATT_VOLT', action='update')).scalar_one()
    assert revision.username == 'names sync'


def test_sync_yes_applies_everything(app, client, sources):
    result = sync(app, '--yes')

    assert '2 of 2 changes applied' in result.output
    db.session.expire_all()
    assert served(client)[-1]['name'] == 'SCD4X_CO2'
    assert 'Nothing to change' in sync(app).output


def test_malformed_names_file_is_reported(app, tmp_path):
    import shutil
    source = tmp_path / 'smartcitizen-data'
    shutil.copytree(DATA, source)
    (source / 'names' / 'SCDevice.json').write_text('[{"name": ')

    report = import_metadata(str(source))

    assert len(report.errors) == 1 and 'SCDevice.json: cannot be read as JSON' in report.errors[0]
    assert report.created['names'] == 0 and report.created['hardware'] == 2
