import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import scflows.tasks.dprocess as dprocess_module
from scflows import db, editing
from scflows.config import config
from scflows.metadata import import_metadata
from scflows.runlog import STAMPED, unstamped
from scflows.tasks.dprocess import dprocess

from conftest import DATA

LATEST = datetime(2025, 11, 28, tzinfo=timezone.utc)


class FakeDevice:
    ''' scdata.Device replacement recording what dprocess does '''
    options = {}

    def __init__(self, params, **kwargs):
        self.params = params
        hardware = FakeDevice.options.get('hardware', 'SCAS_TEST1')
        postprocessing = SimpleNamespace(hardware_url=f'https://flows/api/v1/hardware/{hardware}.json')
        self.handler = SimpleNamespace(postprocessing={'latest_postprocessing': LATEST},
                                       json=SimpleNamespace(name='Kit 1', postprocessing=postprocessing))
        self.kwargs = kwargs
        self.blueprint = 'sc_air'
        self.used_blueprint = None
        self.channels, self.versions = [], []
        self.checks = FakeDevice.options.get('checks', ['GAPS'])
        self.health = {}
        self.options = SimpleNamespace(min_date=None, channels=[], limit=None)
        self.required_sensors = FakeDevice.options.get('required_sensors', ['ADC_48_2', 'TEMP'])
        self.valid_for_processing = FakeDevice.options.get('valid', True)
        self.data = pd.DataFrame()
        self.loaded = False
        self.processed = False
        self.postprocessing_updated = False
        self.posted = None
        # What each load asked for, and the order of loads and posts
        self.loads, self.calls = [], []
        FakeDevice.instance = self

    def use_blueprint(self, name, blueprint):
        self.blueprint = name
        self.used_blueprint = blueprint
        self.channels = [SimpleNamespace(name=channel['name'], function=channel['function'],
                                         kwargs=dict(channel.get('kwargs') or {})) for channel in blueprint['channels']]
        return True

    def __sensor_map__(self):
        return FakeDevice.options.get('sensor_map', {})

    async def load(self):
        self.loads.append({name: getattr(self.options, name, None) for name in ('channels', 'min_date', 'max_date', 'limit')})
        self.calls.append('load')
        # The first load is the run's; a second one, the sensors of the checks
        first = len(self.loads) == 1
        self.loaded = FakeDevice.options.get('load', True) if first else FakeDevice.options.get('check_load', True)
        if self.loaded:
            self.data = FakeDevice.options.get('data', pd.DataFrame({'TEMP': [20.0]})) if first \
                else FakeDevice.options.get('check_data', pd.DataFrame())
        return self.loaded

    def process(self):
        if FakeDevice.options.get('process_raises'):
            raise ValueError('channels with a cycle')
        self.processed = FakeDevice.options.get('process', True)
        return self.processed

    def health_checks(self):
        if FakeDevice.options.get('checks_fail'):
            raise ValueError('bad check')
        self.health = {'start': None, 'end': None, 'rows': len(self.data),
                       'checks': [{'name': 'GAPS', 'status': 'ok',
                                   'columns': {column: {'status': 'ok', 'rows': len(self.data)} for column in self.data}}]}

    def update_postprocessing_date(self):
        self.postprocessing_updated = FakeDevice.options.get('postprocessing_updated', True)

    async def post(self, **kwargs):
        self.posted = kwargs
        self.calls.append('post')
        # What the Smart Citizen API client records
        self.handler.posted = {'SO2': {'id': 123, 'readings': 1000}}
        self.handler.not_posted = {'SO2_WE': 'no sensor id', 'SO2_AE': 'no sensor id'}
        return FakeDevice.options.get('post', True)


@pytest.fixture
def hardware(app):
    ''' SCAS_TEST1, with test_air as its process blueprint '''
    import_metadata(DATA)


@pytest.fixture
def refreshed(monkeypatch):
    calls = []
    monkeypatch.setattr(dprocess_module, 'refresh_metadata', lambda: calls.append(True))
    return calls


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv('SC_BEARER', 'test-token')


@pytest.fixture
def run(monkeypatch, refreshed, hardware):
    monkeypatch.setattr(dprocess_module.sc, 'Device', FakeDevice)

    def run_dprocess(dry_run=False, **options):
        FakeDevice.options = options
        _, state, health = asyncio.run(dprocess(1, dry_run=dry_run))
        FakeDevice.instance.returned_health = health
        return state, FakeDevice.instance

    return run_dprocess


def test_success(run):
    state, device = run(dry_run=True)

    assert state == ['SUCCESS', 'PROCESSED AND UPLOADED']
    assert device.options.min_date == LATEST
    assert device.options.channels == ['ADC_48_2', 'TEMP']
    assert device.options.limit == config._max_load_amount
    assert device.posted['columns'] == 'channels'
    assert device.posted['dry_run'] is True
    assert device.posted['with_postprocessing'] is True


def test_the_log_says_what_is_posted(monkeypatch, refreshed, hardware):
    monkeypatch.setattr(dprocess_module.sc, 'Device', FakeDevice)
    FakeDevice.options = {}

    log, state, _ = asyncio.run(dprocess(1, dry_run=True))
    lines = [unstamped(line) for line in log]

    assert 'info: Would post: SO2 (sensor 123, 1000 readings)' in lines
    assert 'info: Not posted (no sensor id): SO2_WE, SO2_AE' in lines
    assert 'info: Dry run: nothing was posted for device 1' in lines

    log, state, _ = asyncio.run(dprocess(1))
    lines = [unstamped(line) for line in log]

    assert 'info: Posted: SO2 (sensor 123, 1000 readings)' in lines
    assert 'info: Device 1 was posted' in lines


def test_no_sensors_to_load(run):
    state, device = run(required_sensors=[])

    assert state == ['ABORTED', 'NO_SENSORS_TO_LOAD']
    # An empty channel list would load all sensors
    assert device.loaded is False


def test_not_valid_for_processing(run):
    state, device = run(valid=False)

    assert state == ['ABORTED', 'NOT_VALID_FOR_PROCESSING']
    assert device.loaded is False


def test_empty_data(run):
    assert run(load=False)[0] == ['ABORTED', 'EMPTY_DATA']


def test_processing_failed(run):
    state, device = run(process=False)

    assert state == ['ABORTED', 'PROCESSING_FAILED']
    assert device.posted is None


def test_postprocessing_not_updated(run):
    state, device = run(postprocessing_updated=False)

    assert state == ['ABORTED', 'POSTPROCESSING_UPDATE_FAILED']
    assert device.posted is None


def test_posting_failed(run):
    assert run(post=False)[0] == ['FAILED', 'DATA_POSTING_FAILED']


def test_metadata_is_refreshed(run, refreshed):
    run()

    assert refreshed == [True]


def test_health_checks_are_returned(run):
    _, device = run()

    assert device.returned_health == {'start': None, 'end': None, 'rows': 1, 'device_name': 'Kit 1',
                                      'blueprint': 'test_air', 'checks': [{'name': 'GAPS', 'status': 'ok', 'columns': {'TEMP': {'status': 'ok', 'rows': 1}}}]}


def test_health_checks_also_when_processing_fails(run):
    state, device = run(process=False)

    assert state == ['ABORTED', 'PROCESSING_FAILED']
    assert device.returned_health['rows'] == 1


def test_health_checks_when_processing_raises(run):
    state, device = run(process_raises=True)

    assert state[0] == 'FAILED' and state[1].startswith('PROCESSING_ERROR: ')
    assert device.returned_health['rows'] == 1


@pytest.mark.parametrize('options', [{'checks': []}, {'checks_fail': True}, {'load': False}])
def test_no_health_without_checks_or_data(run, options):
    _, device = run(**options)

    assert device.returned_health is None


def test_context_says_what_the_run_used(monkeypatch, refreshed, hardware):
    monkeypatch.setattr(dprocess_module.sc, 'Device', FakeDevice)
    FakeDevice.options = {}
    context = {}

    log, state, _ = asyncio.run(dprocess(1, dry_run=True, context=context))
    assert all(STAMPED.fullmatch(line) for line in log)
    log = [unstamped(line) for line in log]

    assert state == ['SUCCESS', 'PROCESSED AND UPLOADED']
    assert context['device_name'] == 'Kit 1' and context['blueprint'] == 'test_air'
    assert context['sensors'] == ['ADC_48_2', 'TEMP'] and context['rows'] == 1
    assert 'info: Device Kit 1, last reading None' in log and any(line.startswith('info: Posting') for line in log)


def test_errors_keep_the_log(monkeypatch, refreshed):
    def broken(**kwargs):
        raise ConnectionError('API down')

    monkeypatch.setattr(dprocess_module.sc, 'Device', broken)

    log, state, health = asyncio.run(dprocess(1))

    assert state == ['FAILED', 'ConnectionError: API down'] and health is None
    assert unstamped(log[-1]) == 'error: ConnectionError: API down'


@pytest.mark.parametrize('value', [None, '', '  '])
def test_without_a_token_only_dry_runs_process(run, monkeypatch, value):
    if value is None:
        monkeypatch.delenv('SC_BEARER')
    else:
        monkeypatch.setenv('SC_BEARER', value)
    FakeDevice.instance = None

    log, state, health = asyncio.run(dprocess(1))

    # Nothing is loaded nor posted
    assert state == ['ABORTED', 'NO_TOKEN'] and FakeDevice.instance is None and health is None
    assert [unstamped(line) for line in log] == ['error: SC_BEARER is not set: posting is disabled. Only dry runs can process devices.']
    # Dry runs still process
    assert run(dry_run=True)[0] == ['SUCCESS', 'PROCESSED AND UPLOADED']


def test_processed_with_the_process_blueprint_of_the_hardware(run):
    state, device = run(dry_run=True)

    assert state == ['SUCCESS', 'PROCESSED AND UPLOADED']
    assert device.blueprint == 'test_air'
    assert device.used_blueprint == editing.find('blueprint', 'test_air').body


def test_the_device_is_loaded_with_the_process_blueprint(run):
    _, device = run(dry_run=True)

    assert device.kwargs == {'blueprint_kind': 'process'}


def test_processed_with_the_process_parameters_of_the_hardware(run):
    hardware = editing.find('hardware', 'SCAS_TEST1')
    channel = editing.find('blueprint', 'test_air').body['channels'][0]['name']
    hardware.process_parameters = {channel: {'function': 'poly_ts', 'coefficients': [2]}}
    # Long processing parameters are not for processing
    hardware.parameters = {channel: {'lam': 1e9}}
    db.session.commit()

    _, device = run(dry_run=True)

    assert device.channels[0].function == 'poly_ts'
    assert device.channels[0].kwargs['coefficients'] == [2] and 'lam' not in device.channels[0].kwargs


def lookback_of(hours):
    hardware = editing.find('hardware', 'SCAS_TEST1')
    hardware.process_parameters = {editing.find('blueprint', 'test_air').body['channels'][0]['name']: {'lookback_hours': hours}}
    db.session.commit()


def test_the_lookback_is_processed_and_only_the_new_readings_are_posted(run):
    lookback_of(24)
    index = pd.date_range(LATEST - pd.Timedelta(hours=24), periods=30, freq='1h')

    state, device = run(dry_run=True, data=pd.DataFrame({'TEMP': 20.0}, index=index))

    assert state == ['SUCCESS', 'PROCESSED AND UPLOADED']
    assert pd.Timestamp(device.options.min_date) == LATEST - pd.Timedelta(hours=24)
    assert device.options.limit == config._max_load_amount + 24 * 60
    # The reading at the latest postprocessing is new
    assert len(device.data) == 6 and device.data.index.min() == LATEST


def test_only_the_lookback_is_no_new_data(run):
    lookback_of(24)
    index = pd.date_range(LATEST - pd.Timedelta(hours=24), LATEST - pd.Timedelta(hours=1), freq='1h')

    state, device = run(dry_run=True, data=pd.DataFrame({'TEMP': 20.0}, index=index))

    assert state == ['ABORTED', 'NO_NEW_DATA'] and device.posted is None


@pytest.mark.parametrize('hardware_name', ['NOT_IN_FLOWS', 'SCAS_TEST1'])
def test_not_processed_without_a_process_blueprint_in_flows(run, hardware_name):
    if hardware_name == 'SCAS_TEST1':
        # A hardware with only a backup blueprint
        body = editing.find('blueprint', 'test_air').body
        editing.save_blueprint('test_backup', dict(body, meta={'kind': 'backup'}))
        item = editing.find('hardware', 'SCAS_TEST1')
        item.blueprints = [editing.find('blueprint', 'test_backup')]
        db.session.commit()

    state, device = run(hardware=hardware_name)

    assert state == ['ABORTED', 'NO_PROCESS_BLUEPRINT']
    assert device.used_blueprint is None and device.loaded is False and device.posted is None


# Sensors that only the checks name

CHECKS = [SimpleNamespace(name='FLAT', kwargs={'columns': ['SEN5X_PM_25', 'TEMP', 'NO2', 'ADC_48_3', 'ADC_49_0']}),
          SimpleNamespace(name='GAPS', kwargs={'gap_sizes': [{'columns': ['NOISE_A'], 'gap_size_minutes': 10}]})]
SENSOR_MAP = {'SEN5X_PM_25': 'SEN5X PM2.5', 'NOISE_A': 'ICS43432 Noise', 'TEMP': 'TEMP', 'NO2': 'Alphasense NO2',
              'ADC_48_3': 'ADC_48_3', 'ADC_49_0': 'ADC_49_0'}


def readings(start, hours, **columns):
    return pd.DataFrame(columns or {'TEMP': 20.0}, index=pd.date_range(start, periods=hours, freq='1h'))


def test_the_sensors_of_the_checks_are_loaded_after_posting_for_the_new_readings(run):
    data = readings(LATEST, 5)
    check_data = readings(LATEST, 5, **{'SEN5X_PM_25': 3.0, 'NOISE_A': 50.0})

    state, device = run(dry_run=True, checks=CHECKS, sensor_map=SENSOR_MAP, data=data, check_data=check_data)

    assert state == ['SUCCESS', 'PROCESSED AND UPLOADED']
    # TEMP is loaded for processing, NO2 is a channel of the blueprint (as posted, not a sensor to check), and
    # SCAS_TEST1 has sensors on ADC_48_3, ADC_48_2 (AS_48_32), ADC_49_2 and ADC_49_3 (PT_49_23), not on ADC_49_0
    assert device.loads[1] == {'channels': ['ADC_48_3', 'ICS43432 Noise', 'SEN5X PM2.5'], 'min_date': data.index.min().isoformat(),
                               'max_date': data.index.max().isoformat(), 'limit': config._max_load_amount}
    assert device.calls == ['load', 'post', 'load']
    assert set(device.returned_health['checks'][0]['columns']) == {'TEMP', 'SEN5X_PM_25', 'NOISE_A'}
    # The run keeps its data and options
    assert list(device.data.columns) == ['TEMP'] and device.options.channels == ['ADC_48_2', 'TEMP']


def test_with_a_lookback_the_sensors_of_the_checks_are_loaded_from_the_new_readings(run):
    lookback_of(24)
    data = readings(LATEST - pd.Timedelta(hours=24), 30)

    _, device = run(dry_run=True, checks=CHECKS, sensor_map=SENSOR_MAP, data=data,
                    check_data=readings(LATEST, 6, **{'SEN5X_PM_25': 3.0}))

    assert pd.Timestamp(device.loads[1]['min_date']) == LATEST
    assert pd.Timestamp(device.loads[1]['max_date']) == data.index.max()
    assert device.loads[1]['limit'] == config._max_load_amount


def test_without_sensors_only_for_the_checks_there_is_one_load(run):
    _, device = run(dry_run=True, checks=CHECKS, sensor_map={'TEMP': 'TEMP'})

    assert len(device.loads) == 1


def test_the_health_is_kept_if_the_sensors_of_the_checks_are_not_loaded(run):
    state, device = run(dry_run=True, checks=CHECKS, sensor_map=SENSOR_MAP, data=readings(LATEST, 5), check_load=False)

    assert state == ['SUCCESS', 'PROCESSED AND UPLOADED']
    assert set(device.returned_health['checks'][0]['columns']) == {'TEMP'}
