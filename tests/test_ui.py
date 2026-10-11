import re

import pytest
from flask import g

from scflows import db
from scflows.metadata import import_metadata
from scflows.models import Calibration, Hardware, Revision

from conftest import DATA, USERS


@pytest.fixture
def client(app):
    import_metadata(DATA)
    return app.test_client()


def sign_in(client, role):
    user = next(user for user in USERS.values() if user['role'] == role)
    # The test app context outlives requests: drop the user cached by Flask-Login
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user)
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def form(**values):
    return dict({'csrf': 'token'}, **values)


def hardware_form(**values):
    data = {'blueprint': 'test_air', 'description': '1SEN55-2ELEC-AFE', 'comment': 'Test kit', 'forwarding': '',
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
    sign_in(client, 'researcher')

    page = client.get('/metadata/').get_data(as_text=True)

    assert '/metadata/hardware/SCAS_TEST1' in page
    assert '/metadata/calibrations/10-002911' in page


def test_edit_hardware_form(client):
    sign_in(client, 'researcher')

    page = client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)

    assert 'AS_48_32=212830246' in page
    assert 'value="2024-04-01"' in page
    assert 'Delete' not in page


def test_check_hardware(client):
    sign_in(client, 'researcher')

    page = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(action='check')).get_data(as_text=True)

    assert 'channels not in blueprint test_air: NO2_AE' in page
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'


def test_save_hardware(client):
    sign_in(client, 'researcher')

    response = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(description='Updated'))

    assert response.status_code == 302
    assert hardware('SCAS_TEST1').description == 'Updated'
    revision = db.session.execute(db.select(Revision).filter_by(key='SCAS_TEST1', action='update')).scalar_one()
    assert revision.username == 'researcher'
    assert 'saved with 2 warnings' in client.get(response.headers['Location']).get_data(as_text=True)


def test_invalid_hardware_is_not_saved(client):
    sign_in(client, 'researcher')

    page = client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(
        description='Updated', version_0_ids='AS_48_32=730002320')).get_data(as_text=True)

    assert 'unknown Alphasense sensor code 730' in page
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'


def test_add_and_remove_versions(client):
    sign_in(client, 'researcher')

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
    sign_in(client, 'researcher')

    assert client.post('/metadata/hardware/new', data=hardware_form(name='SCAS_NEW')).status_code == 302
    assert hardware('SCAS_NEW').blueprint.name == 'test_air'

    page = client.post('/metadata/hardware/new', data=hardware_form(name='SCAS_NEW')).get_data(as_text=True)
    assert 'SCAS_NEW already exists' in page
    page = client.post('/metadata/hardware/new', data=hardware_form(name='bad name')).get_data(as_text=True)
    assert 'letters, numbers' in page


def test_edit_calibration(client):
    sign_in(client, 'researcher')
    assert 'name="t20"' in client.get('/metadata/calibrations/10-002911').get_data(as_text=True)

    response = client.post('/metadata/calibrations/10-002911', data=form(t20='21', v20='0.31', action='save'))

    assert response.status_code == 302
    db.session.expire_all()
    calibration = db.session.execute(db.select(Calibration).filter_by(sensor_id='10-002911')).scalar_one()
    assert calibration.data == {'t20': 21, 'v20': 0.31}


def test_new_calibration(client):
    sign_in(client, 'researcher')
    page = client.get('/metadata/calibrations/new?kind=afe_board').get_data(as_text=True)
    assert 'name="v20"' in page and 'name="we_sensor_zero_mv"' not in page

    response = client.post('/metadata/calibrations/new', data=form(sensor_id='10-000001', kind='afe_board',
                                                                    t20='20', v20='', action='save'))

    assert response.status_code == 302
    calibration = db.session.execute(db.select(Calibration).filter_by(sensor_id='10-000001')).scalar_one()
    assert (calibration.kind, calibration.data) == ('afe_board', {'t20': 20, 'v20': ''})


def test_forms_require_csrf_token(client):
    sign_in(client, 'researcher')

    response = client.post('/metadata/hardware/SCAS_TEST1', data=dict(hardware_form(description='x'), csrf='other'))

    assert response.status_code == 400
    assert hardware('SCAS_TEST1').description == '1SEN55-2ELEC-AFE'


def test_delete_requires_admin(client):
    sign_in(client, 'researcher')
    assert client.post('/metadata/hardware/SCAS_TEST1/delete', data=form()).status_code == 403

    sign_in(client, 'admin')
    assert 'Delete' in client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)
    assert client.post('/metadata/hardware/SCAS_TEST1/delete', data=form()).status_code == 302
    assert hardware('SCAS_TEST1') is None


def test_history(client):
    sign_in(client, 'researcher')
    client.post('/metadata/hardware/SCAS_TEST1', data=hardware_form(description='Updated'))

    page = client.get('/metadata/hardware/SCAS_TEST1/history').get_data(as_text=True)

    assert re.search(r'<td>update</td>\s*<td>researcher</td>', page)
    assert '<td>import</td>' in page
