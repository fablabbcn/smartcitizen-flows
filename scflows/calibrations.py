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

# Sheets round their values: mV/ppb within 1% of nA/ppb × gain, total zeros within 2 mV
SENSITIVITY_TOLERANCE = 0.01
ZERO_TOLERANCE_MV = 2.0


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


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def short(value):
    return f'{value:.4g}'


def warnings(data):
    '''
    Values of an Alphasense sensor calibration that do not add up: sensitivities in mV/ppb against those in nA/ppb
    times the PCB gain, and total zeros against electronic plus sensor zeros. Missing or empty values are not checked
    '''
    result = []
    gain = number(data.get('pcb_gain_mv_na'))
    for label, na_field, mv_field in (('WE sensitivity', 'we_sensitivity_na_ppb', 'we_sensitivity_mv_ppb'),
                                      ('NO2 cross sensitivity', 'we_cross_sensitivity_no2_na_ppb',
                                       'we_cross_sensitivity_no2_mv_ppb')):
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
