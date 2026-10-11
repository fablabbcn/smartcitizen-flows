import re

import pytest
from flask import g

from scflows import db
from scflows.metadata import import_metadata
from scflows.models import Calibration, Hardware, Revision, SensorName

from conftest import DATA, USERS


@pytest.fixture
def client(app):
    import_metadata(DATA)
    return app.test_client()


def sign_in(client, role, hardware=()):
    user = dict(next(user for user in USERS.values() if user['role'] == role), hardware=list(hardware))
    # The test app context outlives requests: drop the user cached by Flask-Login
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user)
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def form(**values):
    return dict({'csrf': 'token'}, **values)


def hardware_form(**values):
    data = {'blueprints': 'test_air', 'description': '1SEN55-2ELEC-AFE', 'comment': 'Test kit', 'forwarding': '',
            'version_count': '1', 'version_0_from': '2024-04-01', 'version_0_to': '',
            'version_0_ids': 'AS_48_32=212830246\nPT_49_23=10-002911\n', 'action': 'save'}
    return form(**dict(data, **values))


def hardware(name):
    db.session.expire_all()
    return db.session.execute(db.select(Hardware).filter_by(name=name)).scalar_one_or_none()


@pytest.mark.parametrize('role, status', [(None, 302), ('citizen', 403), ('researcher', 200), ('admin', 200)])
def test_access(client, role, status):
    if role:
        sign_in(client, role)

    assert client.get('/metadata/').status_code == status


def test_index_lists_metadata(client):
    sign_in(client, 'admin')

    page = client.get('/metadata/').get_data(as_text=True)

    assert '/metadata/hardware/SCAS_TEST1' in page
    assert '/metadata/calibrations/10-002911' in page


def test_edit_hardware_form(client):
    sign_in(client, 'admin')

    page = client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)

    assert 'AS_48_32=212830246' in page
    assert 'value="2024-04-01"' in page
    assert 'Delete' in page and 'value="save"' in page


def test_check_hardware(client):
    sign_in(client, 'admin')

    page = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(action='check')).get_data(as_text=True)

    assert 'channels not in blueprint test_air: NO2_AE' in page
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'


def test_save_hardware(client):
    sign_in(client, 'admin')

    response = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(description='Updated'))

    assert response.status_code == 302
    assert hardware('SCAS_TEST1').description == 'Updated'
    revision = db.session.execute(db.select(Revision).filter_by(key='SCAS_TEST1', action='update')).scalar_one()
    assert revision.username == 'admin'
    assert 'saved with 2 warnings' in client.get(response.headers['Location']).get_data(as_text=True)


def test_invalid_hardware_is_not_saved(client):
    sign_in(client, 'admin')

    page = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(
        description='Updated', version_0_ids='AS_48_32=730002320')).get_data(as_text=True)

    assert 'unknown Alphasense sensor code 730' in page
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'


def test_add_and_remove_versions(client):
    sign_in(client, 'admin')

    page = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(action='add_version')).get_data(as_text=True)
    assert 'name="version_1_from"' in page

    client.post('/metadata/hardware/SCAS_TEST2', data=hardware_form(
        version_count='2', version_0_to='2024-06-01', version_1_from='2024-06-01', version_1_to='',
        version_1_ids='AS_48_32=212830246'))
    assert len(hardware('SCAS_TEST2').versions) == 2

    client.post('/metadata/hardware/SCAS_TEST2', data=hardware_form(
        version_count='2', version_0_to='2024-06-01', version_1_from='2024-06-01', version_1_ids='AS_48_32=212830246',
        version_1_remove='on'))
    assert len(hardware('SCAS_TEST2').versions) == 1


def test_new_hardware(client):
    sign_in(client, 'admin')

    assert client.post('/metadata/hardware/new', data=hardware_form(name='SCAS_NEW')).status_code == 302
    assert hardware('SCAS_NEW').blueprint.name == 'test_air'

    page = client.post('/metadata/hardware/new', data=hardware_form(name='SCAS_NEW')).get_data(as_text=True)
    assert 'SCAS_NEW already exists' in page
    page = client.post('/metadata/hardware/new', data=hardware_form(name='bad name')).get_data(as_text=True)
    assert 'letters, numbers' in page


def test_edit_calibration(client):
    sign_in(client, 'admin')
    assert 'name="t20"' in client.get('/metadata/calibrations/10-002911').get_data(as_text=True)

    response = client.post('/metadata/calibrations/10-002911', data=form(t20='21', v20='0.31', action='save'))

    assert response.status_code == 302
    db.session.expire_all()
    calibration = db.session.execute(db.select(Calibration).filter_by(sensor_id='10-002911')).scalar_one()
    assert calibration.data == {'t20': 21, 'v20': 0.31}


def test_new_calibration(client):
    sign_in(client, 'admin')
    page = client.get('/metadata/calibrations/new?kind=afe_board').get_data(as_text=True)
    assert 'name="v20"' in page and 'name="we_sensor_zero_mv"' not in page

    response = client.post('/metadata/calibrations/new', data=form(sensor_id='10-000001', kind='afe_board',
                                                                    t20='20', v20='', action='save'))

    assert response.status_code == 302
    calibration = db.session.execute(db.select(Calibration).filter_by(sensor_id='10-000001')).scalar_one()
    assert (calibration.kind, calibration.data) == ('afe_board', {'t20': 20, 'v20': ''})


def test_forms_require_csrf_token(client):
    sign_in(client, 'admin')

    response = client.post('/metadata/hardware/SCAS_TEST1', data=dict(hardware_form(description='x'), csrf='other'))

    assert response.status_code == 400
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'


def test_delete_requires_admin(client):
    sign_in(client, 'researcher', ['SCAS_TEST1'])
    assert client.post('/metadata/hardware/SCAS_TEST1/delete', data=form()).status_code == 403

    sign_in(client, 'admin')
    assert 'Delete' in client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)
    assert client.post('/metadata/hardware/SCAS_TEST1/delete', data=form()).status_code == 302
    assert hardware('SCAS_TEST1') is None


def test_history(client):
    sign_in(client, 'admin')
    client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(description='Updated'))

    page = client.get('/metadata/hardware/SCAS_TEST1/history').get_data(as_text=True)

    assert re.search(r'<span class="badge update">update</span>\s*<strong>admin</strong>', page)
    assert 'Changed: <code>description</code>' in page
    assert '<span class="badge import">import</span>' in page


# Researchers: the metadata of their devices, read only

def test_researcher_sees_the_metadata_of_their_devices(client):
    sign_in(client, 'researcher', ['SCAS_TEST1', 'NOT_IN_FLOWS'])

    page = client.get('/metadata/').get_data(as_text=True)

    assert '/metadata/hardware/SCAS_TEST1' in page and '/metadata/hardware/SCAS_TEST2' not in page
    # Calibrations of the sensors in SCAS_TEST1
    assert '/metadata/calibrations/10-002911' in page
    assert 'New hardware' not in page and 'read only' in page


def test_researcher_without_hardware(client):
    sign_in(client, 'researcher')

    page = client.get('/metadata/').get_data(as_text=True)

    assert '/metadata/hardware/' not in page and 'None of your devices uses hardware in flows' in page


def test_researcher_forms_are_read_only(client):
    sign_in(client, 'researcher', ['SCAS_TEST1'])

    page = client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)
    assert 'AS_48_32=212830246' in page and '<fieldset class="plain" disabled>' in page
    assert 'value="save"' not in page and 'Delete' not in page
    assert 'Read only' in client.get('/metadata/calibrations/10-002911').get_data(as_text=True)
    assert client.get('/metadata/hardware/SCAS_TEST1/history').status_code == 200


@pytest.mark.parametrize('path', ['/metadata/hardware/SCAS_TEST2', '/metadata/hardware/SCAS_TEST2/history',
                                  '/metadata/calibrations/202760040'])
def test_researcher_cannot_see_other_metadata(client, path):
    # 202760040 is only in SCAS_TEST2
    sign_in(client, 'researcher', ['SCAS_TEST1'])

    response = client.get(path)

    assert response.status_code == 403
    assert 'hardware of their devices' in response.get_data(as_text=True)


@pytest.mark.parametrize('path, data', [
    ('/metadata/hardware/SCAS_TEST1', hardware_form(description='Updated')),
    ('/metadata/calibrations/10-002911', form(t20='21', v20='0.31', action='save')),
    ('/metadata/hardware/new', hardware_form(name='SCAS_NEW')),
    ('/metadata/calibrations/new', form(sensor_id='10-000001', kind='afe_board', t20='20', action='save')),
])
def test_researcher_cannot_change_metadata(client, path, data):
    sign_in(client, 'researcher', ['SCAS_TEST1'])

    assert client.post(path, data=data).status_code == 403
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'
    assert hardware('SCAS_NEW') is None


# Sensor names

def test_names_tab(client):
    sign_in(client, 'researcher', ['SCAS_TEST1'])

    page = client.get('/metadata/').get_data(as_text=True)

    assert '/metadata/names/TEMP' in page and 'data-tab="names"' in page
    # Id 79 has two names in the test data
    assert 'Ids with several names' in page and 'GB_TEMP, SHT31_EXT_TEMP' in page
    assert 'New name' not in page


def test_admin_edits_a_name(client):
    sign_in(client, 'admin')
    assert 'name="unit"' in client.get('/metadata/names/TEMP').get_data(as_text=True)

    response = client.post('/metadata/names/TEMP', data=form(id='55', description='Air temperature', unit='C',
                                                             action='save'))

    assert response.status_code == 302
    db.session.expire_all()
    item = db.session.execute(db.select(SensorName).filter_by(name='TEMP')).scalar_one()
    assert (item.sensor_id, item.description) == (55, 'Air temperature')
    page = client.get('/metadata/name/TEMP/history').get_data(as_text=True)
    assert 'Changed: <code>description</code>' in page


def test_invalid_name_form(client):
    sign_in(client, 'admin')

    page = client.post('/metadata/names/TEMP', data=form(id='x', action='save')).get_data(as_text=True)

    assert 'id: Input should be a valid integer' in page


def test_new_and_delete_name(client):
    sign_in(client, 'admin')

    assert client.post('/metadata/names/new', data=form(name='SCD4X_CO2', id='258', description='SCD4X CO2',
                                                        unit='ppm', action='save')).status_code == 302
    page = client.post('/metadata/names/new', data=form(name='SCD4X_CO2', id='258')).get_data(as_text=True)
    assert 'SCD4X_CO2 already exists' in page

    assert client.post('/metadata/name/SCD4X_CO2/delete', data=form()).status_code == 302
    assert db.session.execute(db.select(SensorName).filter_by(name='SCD4X_CO2')).scalar_one_or_none() is None


def test_shared_id_warning(client):
    sign_in(client, 'admin')

    page = client.get('/metadata/names/SHT31_EXT_TEMP').get_data(as_text=True)

    assert 'also has the name GB_TEMP. Processing uses GB_TEMP' in page


def test_researcher_sees_names_read_only(client):
    sign_in(client, 'researcher')

    page = client.get('/metadata/names/TEMP').get_data(as_text=True)
    assert '<fieldset class="plain" disabled>' in page and 'Delete' not in page

    assert client.post('/metadata/names/TEMP', data=form(id='1', action='save')).status_code == 403
    assert client.get('/metadata/names/new').status_code == 403
    assert client.post('/metadata/name/TEMP/delete', data=form()).status_code == 403


# Parameters

def test_parameters_tab_and_page(client):
    sign_in(client, 'admin')
    assert '/metadata/parameters/ASB4_NO2' in client.get('/metadata/').get_data(as_text=True)

    response = client.post('/metadata/parameters/ASB4_NO2', data=form(channels='{"NO2": {"lam": 1e9}}',
                                                                       description='Tuned', action='save'))
    assert response.status_code == 302
    page = client.get('/metadata/parameters/ASB4_NO2').get_data(as_text=True)
    assert '&#34;lam&#34;: 1000000000.0' in page and 'History' in page

    page = client.post('/metadata/parameters/ASB4_NO2', data=form(channels='{bad', action='save')).get_data(as_text=True)
    assert 'channels: not valid JSON' in page
    assert client.get('/metadata/parameters/NOT_A_TYPE').status_code == 404


def test_researcher_sees_parameters_read_only(client):
    sign_in(client, 'researcher')

    assert '<fieldset class="plain" disabled>' in client.get('/metadata/parameters/SCD30').get_data(as_text=True)
    assert client.post('/metadata/parameters/SCD30', data=form(channels='{}', action='save')).status_code == 403


def test_hardware_parameters_field(client):
    sign_in(client, 'admin')

    response = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(
        parameters='{"O3": {"function": "alphasense_803_04"}}'))
    assert response.status_code == 302
    assert hardware('SCAS_TEST1').parameters == {'O3': {'function': 'alphasense_803_04'}}

    page = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(parameters='{bad')).get_data(as_text=True)
    assert 'parameters: Input should be a valid dictionary' in page
