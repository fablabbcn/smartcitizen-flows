''' Calibrations of Alphasense sensors and AFE boards: their fields, in the order of the Alphasense sheets, and checks '''
import re

# AFE board serials: two digits, a dash and a number (10-002911, 12-000445, 17-...). Alphasense sensors are numbers
AFE_BOARD_ID = re.compile(r'^\d{2}-\d+$')

# (field, label) in the order of the Alphasense calibration sheet, in groups
ALPHASENSE_GROUPS = [
    ('Zero offsets', [
        ('we_electronic_zero_mv', 'WE electronic zero (mV)'),
        ('we_sensor_zero_mv', 'WE sensor zero (mV)'),
        ('we_total_zero_mv', 'WE total zero (mV)'),
        ('ae_electronic_zero_mv', 'AE electronic zero (mV)'),
        ('ae_sensor_zero_mv', 'AE sensor zero (mV)'),
        ('ae_total_zero_mv', 'AE total zero (mV)'),
    ]),
    ('Gain', [
        ('pcb_gain_mv_na', 'PCB gain (mV/nA)'),
    ]),
    ('Sensitivity', [
        ('we_sensitivity_na_ppb', 'WE sensitivity (nA/ppb)'),
        ('we_sensitivity_mv_ppb', 'WE sensitivity (mV/ppb)'),
        ('we_cross_sensitivity_no2_na_ppb', 'NO2 cross sensitivity (nA/ppb)'),
        ('we_cross_sensitivity_no2_mv_ppb', 'NO2 cross sensitivity (mV/ppb)'),
    ]),
]
AFE_GROUPS = [
    ('PT1000', [
        ('t20', 'T20 (°C)'),
        ('v20', 'V20 (V)'),
    ]),
]

# Sensitivities in mV/ppb are those in nA/ppb times the PCB gain: (label, nA/ppb field, mV/ppb field)
SENSITIVITY_ROWS = [
    ('WE sensitivity', 'we_sensitivity_na_ppb', 'we_sensitivity_mv_ppb'),
    ('NO2 cross sensitivity', 'we_cross_sensitivity_no2_na_ppb', 'we_cross_sensitivity_no2_mv_ppb'),
]

# PCB gain (mV/nA) by gas: the boards are the same for each group of sensors (ISB 000-0ISB-00 for CO, SO2 and
# H2S; 000-0ISB-02 for NO2 and OX), and all the calibrations so far agree for CO, SO2, NO2 and OX, A and B
# series. NO has a board of its own: not checked
EXPECTED_GAINS = {'CO': 0.8, 'SO2': 0.8, 'H2S': 0.8, 'NO2': -0.73, 'OX': -0.73}

# Sheets round their values: mV/ppb within 1% of nA/ppb × gain, total zeros within 2 mV
SENSITIVITY_TOLERANCE = 0.01
ZERO_TOLERANCE_MV = 2.0
GAIN_TOLERANCE = 0.005


def groups(kind):
    from .models import Calibration
    return AFE_GROUPS if kind == Calibration.AFE_BOARD else ALPHASENSE_GROUPS


def kind_for_id(sensor_id):
    ''' Kind of calibration a sensor id needs, from its form '''
    from .models import Calibration
    return Calibration.AFE_BOARD if AFE_BOARD_ID.match(sensor_id or '') else Calibration.ALPHASENSE_SENSOR


def sensor_type(sensor_id):
    '''
    (label, title) of a sensor id from its code (smartcitizen-connector), e.g. ('SO2-B4', 'Alphasense B-series SO2
    sensor (code 164)'), ('AFE board', ...), or None if the code is unknown
    '''
    from smartcitizen_connector._config import config
    sensor_id = sensor_id or ''
    if AFE_BOARD_ID.match(sensor_id):
        code = sensor_id.split('-')[0]
        if code in getattr(config, '_afe_board_codes', ()):
            return 'AFE board', f'Alphasense AFE board, for A-series sensors with a PT1000 (code {code})'
        return None
    code = sensor_id[:3]
    sensor = config._as_sensor_codes.get(code)
    if sensor is None:
        return None
    model = getattr(config, '_as_sensor_models', {}).get(sensor, sensor)
    # ASB4_SO2: B-series SO2
    series, gas = sensor[2], sensor.split('_', 1)[1]
    return model, f'Alphasense {series}-series {gas} sensor (code {code})'


def expected_gain(sensor_id):
    ''' (PCB gain in mV/nA, model) usual for the type of a sensor id, or None if unknown '''
    from smartcitizen_connector._config import config
    sensor = config._as_sensor_codes.get((sensor_id or '')[:3])
    if sensor is None or (sensor_id or '')[3:4] == '-':
        return None
    gain = EXPECTED_GAINS.get(sensor.split('_', 1)[1])
    return (gain, sensor_type(sensor_id)[0]) if gain is not None else None


def code_table():
    '''
    The codes for the interface to label ids as they are typed:
    {sensors: {code: [model, title, usual gain or null]}, afe: [codes]}
    '''
    from smartcitizen_connector._config import config
    sensors = {}
    for code in config._as_sensor_codes:
        gain = expected_gain(code + '000000')
        sensors[code] = list(sensor_type(code + '000000')) + [gain[0] if gain else None]
    return {'sensors': sensors, 'afe': list(getattr(config, '_afe_board_codes', ()))}


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def short(value):
    return f'{value:.4g}'


def warnings(data, sensor_id=None):
    '''
    Values of an Alphasense sensor calibration that do not add up: the PCB gain against the usual one for the
    type of sensor (if sensor_id is given), sensitivities in mV/ppb against those in nA/ppb times the PCB gain, and
    total zeros against electronic plus sensor zeros. Missing or empty values are not checked
    '''
    result = []
    gain = number(data.get('pcb_gain_mv_na'))
    usual = expected_gain(sensor_id) if sensor_id else None
    if usual and gain is not None and abs(gain - usual[0]) > GAIN_TOLERANCE:
        result.append(f'PCB gain: {short(gain)} mV/nA, but {usual[1]} sensors have {short(usual[0])} mV/nA')
    for label, na_field, mv_field in SENSITIVITY_ROWS:
        na, mv = number(data.get(na_field)), number(data.get(mv_field))
        if None in (gain, na, mv):
            continue
        expected = na * gain
        if abs(mv - expected) > SENSITIVITY_TOLERANCE * max(abs(expected), abs(mv)):
            result.append(f'{label}: {short(mv)} mV/ppb, but {short(na)} nA/ppb × {short(gain)} mV/nA gives '
                          f'{short(expected)} mV/ppb (more than {SENSITIVITY_TOLERANCE:.0%} apart)')
    for electrode in ('WE', 'AE'):
        prefix = electrode.lower()
        electronic, sensor, total = (number(data.get(f'{prefix}_{part}_zero_mv'))
                                     for part in ('electronic', 'sensor', 'total'))
        if None in (electronic, sensor, total):
            continue
        if abs(total - (electronic + sensor)) > ZERO_TOLERANCE_MV:
            result.append(f'{electrode} total zero: {short(total)} mV, but electronic {short(electronic)} + sensor '
                          f'{short(sensor)} gives {short(electronic + sensor)} mV (more than {short(ZERO_TOLERANCE_MV)} mV apart)')
    return result
