import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import scflows.tasks.dprocess as dprocess_module
from scflows.config import config
from scflows.tasks.dprocess import dprocess

LATEST = datetime(2025, 11, 28, tzinfo=timezone.utc)


class FakeDevice:
    ''' scdata.Device replacement recording what dprocess does '''
    options = {}

    def __init__(self, params, **kwargs):
        self.params = params
        self.handler = SimpleNamespace(postprocessing={'latest_postprocessing': LATEST},
                                       json=SimpleNamespace(name='Kit 1'))
        self.blueprint = 'sc_air'
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
        FakeDevice.instance = self

    async def load(self):
        self.loaded = FakeDevice.options.get('load', True)
        if self.loaded:
            self.data = pd.DataFrame({'TEMP': [20.0]})
        return self.loaded

    def process(self):
        self.processed = FakeDevice.options.get('process', True)
        return self.processed

    def health_checks(self):
        if FakeDevice.options.get('checks_fail'):
            raise ValueError('bad check')
        self.health = {'start': None, 'end': None, 'rows': len(self.data),
                       'checks': [{'name': 'GAPS', 'status': 'ok', 'columns': {}}]}

    def update_postprocessing_date(self):
        self.postprocessing_updated = FakeDevice.options.get('postprocessing_updated', True)

    async def post(self, **kwargs):
        self.posted = kwargs
        return FakeDevice.options.get('post', True)


@pytest.fixture
def refreshed(monkeypatch):
    calls = []
    monkeypatch.setattr(dprocess_module, 'refresh_metadata', lambda: calls.append(True))
    return calls


@pytest.fixture
def run(monkeypatch, refreshed):
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
                                      'blueprint': 'sc_air', 'checks': [{'name': 'GAPS', 'status': 'ok', 'columns': {}}]}


def test_health_checks_also_when_processing_fails(run):
    state, device = run(process=False)

    assert state == ['ABORTED', 'PROCESSING_FAILED']
    assert device.returned_health['rows'] == 1


@pytest.mark.parametrize('options', [{'checks': []}, {'checks_fail': True}, {'load': False}])
def test_no_health_without_checks_or_data(run, options):
    _, device = run(**options)

    assert device.returned_health is None
