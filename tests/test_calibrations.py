''' Alphasense calibrations: fields in sheet order, consistency checks, order numbers, AFE board ids '''
import pytest
from flask import g

from scflows import calibrations, db
from scflows.metadata import import_metadata
from scflows.models import Calibration, Revision
from scflows.validation import check_hardware

from conftest import DATA, USERS, auth

GOOD = {'we_electronic_zero_mv': 342.16, 'we_sensor_zero_mv': 36, 'we_total_zero_mv': 378,
        'ae_electronic_zero_mv': 337.53, 'ae_sensor_zero_mv': 6, 'ae_total_zero_mv': 344,
        'pcb_gain_mv_na': -0.73, 'we_sensitivity_na_ppb': -0.42, 'we_sensitivity_mv_ppb': 0.306,
        'we_cross_sensitivity_no2_na_ppb': 0, 'we_cross_sensitivity_no2_mv_ppb': 0}


@pytest.fixture
def client(app):
    import_metadata(DATA)
    return app.test_client()


def sign_in(client, role='admin'):
    user = next(user for user in USERS.values() if user['role'] == role)
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user, hardware=[], devices=[])
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def calibration(sensor_id):
    db.session.expire_all()
    return db.session.execute(db.select(Calibration).filter_by(sensor_id=sensor_id)).scalar_one()


@pytest.mark.parametrize('sensor_id, kind', [('10-002911', 'afe_board'), ('12-000445', 'afe_board'),
                                              ('17-000123', 'afe_board'), ('212830246', 'alphasense_sensor'),
                                              ('', 'alphasense_sensor'), ('1-2', 'alphasense_sensor'),
                                              # Exactly six ASCII digits after the dash, matched whole
                                              ('10-00291', 'alphasense_sensor'), ('10-0029111', 'alphasense_sensor'),
                                              ('10-002911\n', 'alphasense_sensor'), ('١٠-٠٠٢٩١١', 'alphasense_sensor')])
def test_kind_from_the_id(sensor_id, kind):
    assert calibrations.kind_for_id(sensor_id) == kind


def test_consistent_values_have_no_warnings():
    # Negative gains too: -0.42 nA/ppb x -0.73 mV/nA = 0.3066 mV/ppb
    assert calibrations.warnings(GOOD) == []
    # Missing or empty values are not checked
    assert calibrations.warnings(dict(GOOD, we_total_zero_mv='', pcb_gain_mv_na=None)) == []


def test_sensitivity_must_match_the_gain():
    [warning] = calibrations.warnings(dict(GOOD, we_sensitivity_mv_ppb=0.42))
    assert warning == ('WE sensitivity: 0.42 mV/ppb, but -0.42 nA/ppb × -0.73 mV/nA gives 0.3066 mV/ppb '
                       '(more than 1% apart)')
    assert calibrations.warnings(dict(GOOD, we_cross_sensitivity_no2_mv_ppb=0.1))[0].startswith('NO2 cross sensitivity')


def test_total_zero_must_add_up():
    [warning] = calibrations.warnings(dict(GOOD, we_total_zero_mv=390))
    assert warning == 'WE total zero: 390 mV, but electronic 342.2 + sensor 36 gives 378.2 mV (more than 2 mV apart)'
    # Within 2 mV (sheets round the totals)
    assert calibrations.warnings(dict(GOOD, ae_total_zero_mv=345.5)) == []


def test_form_follows_the_alphasense_sheet(client):
    sign_in(client)
    page = client.get('/metadata/calibrations/212830246').get_data(as_text=True)

    order = [page.index(f'name="{field}"') for _, fields in calibrations.ALPHASENSE_GROUPS for field, _ in fields]
    assert order == sorted(order)
    assert 'WE electronic zero (mV)' in page and 'PCB gain (mV/nA)' in page and 'Order number' in page


def test_new_calibration_for_an_afe_id_opens_the_afe_form(client):
    sign_in(client)
    page = client.get('/metadata/calibrations/new?sensor_id=17-000123').get_data(as_text=True)
    assert 'name="v20"' in page and 'name="we_sensor_zero_mv"' not in page

    response = client.post('/metadata/calibrations/new', data={'csrf': 'token', 'sensor_id': '17-000123', 'kind': 'afe_board',
                                                               't20': '20', 'v20': '0.3', 'action': 'save'})
    assert response.status_code == 302 and calibration('17-000123').kind == 'afe_board'


def test_order_number_is_kept_apart_and_in_the_history(client):
    sign_in(client)
    data = {'csrf': 'token', 'action': 'save', 'order_number': ' PO-2026-118 ',
            **{field: value for field, value in GOOD.items()}}

    client.post('/metadata/calibrations/212830246', data=data)

    item = calibration('212830246')
    assert item.order_number == 'PO-2026-118' and 'order_number' not in item.data
    # Not served to processing
    assert 'order_number' not in client.get('/api/v1/calibrations/calibrations.json').get_json()['212830246']
    revision = db.session.execute(db.select(Revision).filter_by(kind='calibration', key='212830246')
                                  .order_by(Revision.id.desc())).scalars().first()
    assert revision.after['order_number'] == 'PO-2026-118'
    page = client.get('/metadata/calibration/212830246/history').get_data(as_text=True)
    assert 'PO-2026-118' in page
    assert 'PO-2026-118' in client.get('/metadata/').get_data(as_text=True)

    # Emptied
    client.post('/metadata/calibrations/212830246', data=dict(data, order_number=''))
    assert calibration('212830246').order_number is None


def test_saving_inconsistent_values_warns(client):
    sign_in(client)
    data = {'csrf': 'token', 'action': 'save', **dict(GOOD, we_total_zero_mv=390)}

    client.post('/metadata/calibrations/212830246', data=data)
    page = client.get('/metadata/calibrations/212830246').get_data(as_text=True)

    assert 'saved with 1 warnings' in page and 'WE total zero: 390 mV' in page


def test_api_order_number_and_warnings(client, sc_me):
    body = dict(GOOD, we_sensitivity_mv_ppb=0.42, order_number='PO-7')
    response = client.put('/api/v1/calibrations/212830246', json=body, headers=auth('admin-token'))

    assert response.status_code == 200
    assert 'order_number' not in response.get_json()['data']
    assert response.get_json()['warnings'][0].startswith('WE sensitivity')
    assert calibration('212830246').order_number == 'PO-7'
    # Without it, the order number stays
    client.put('/api/v1/calibrations/212830246', json=GOOD, headers=auth('admin-token'))
    assert calibration('212830246').order_number == 'PO-7'
    assert client.put('/api/v1/calibrations/212830246', json=dict(GOOD, order_number=7),
                      headers=auth('admin-token')).status_code == 422


def test_hardware_check_flags_afe_ids_and_calibrations(client):
    import copy, json
    from os.path import join
    with open(join(DATA, 'hardware', 'SCAS_TEST1.json')) as file:
        hardware = json.load(file)
    item = calibration('212830246')
    item.data = dict(GOOD, we_total_zero_mv=390)
    db.session.commit()
    hardware = copy.deepcopy(hardware)
    hardware['versions'][0]['ids']['PT_49_23'] = '10002911'

    warnings = check_hardware(hardware).warnings

    assert 'versions.0.ids.PT_49_23: 10002911 does not look like an AFE board id (e.g. 10-002911)' in warnings
    assert any(warning.startswith('versions.0.ids.AS_48_32: calibration of 212830246: WE total zero') for warning in warnings)


@pytest.mark.parametrize('sensor_id, label', [('164400105', 'SO2-B4'), ('134000001', 'SO2-A4'), ('212830246', 'NO2-A43F'),
                                               ('204042163', 'OX-B431'), ('163040003', 'H2S-B4'), ('133000001', 'H2S-A4'),
                                               ('10-002911', 'AFE board'), ('17-000123', 'AFE board')])
def test_sensor_type_from_the_code(sensor_id, label):
    assert calibrations.sensor_type(sensor_id)[0] == label


def test_unknown_codes_have_no_type():
    assert calibrations.sensor_type('73002320') is None and calibrations.sensor_type('99-000001') is None
    assert calibrations.sensor_type('164400105')[1] == 'Alphasense B-series SO2 sensor (code 164)'


def test_calibration_pages_show_the_type(client):
    sign_in(client)
    page = client.get('/metadata/calibrations/212830246').get_data(as_text=True)
    assert 'title="Alphasense A-series NO2 sensor (code 212)">NO2-A43F</span>' in page
    listing = client.get('/metadata/').get_data(as_text=True)
    assert '>NO2-A43F</span>' in listing and 'AFE board' in listing


def test_forms_carry_the_code_table(client):
    import json, re
    sign_in(client)
    for path in ('/metadata/calibrations/new', '/metadata/hardware/SCAS_TEST1'):
        page = client.get(path).get_data(as_text=True)
        # In the page body, not in the head
        body = page[page.index('<main'):]
        table = json.loads(re.search(r'<script type="application/json" id="sensor-codes">(.*?)</script>', body, re.S).group(1))
        assert table['sensors']['164'] == ['SO2-B4', 'Alphasense B-series SO2 sensor (code 164)']
        assert table['afe'] == ['10', '12', '17']
