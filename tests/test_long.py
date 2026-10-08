import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pyarrow.dataset as ds
import pytest

import scflows.jobs as jobs
import scflows.tasks.dlong as dlong_module
from scflows import db, editing, storage
from scflows.metadata import import_metadata
from scflows.models import Job
from scflows.tasks.dlong import dlong

from conftest import DATA


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv('STORAGE_ROOT', str(tmp_path))
    return tmp_path


def read(root, device_id, blueprint):
    table = ds.dataset(str(root / 'devices' / str(device_id) / 'processed' / blueprint), format='parquet',
                       partitioning='hive').to_table()
    return table.to_pandas().set_index('TIME').sort_index()


# Storage

def frame(start, days, value):
    index = pd.date_range(start, periods=days * 24, freq='1h', tz='UTC')
    return pd.DataFrame({'CO2': float(value)}, index=index)


def test_write_processed_replaces_the_months_it_covers(root):
    assert storage.write_processed(1, 'long', frame('2026-01-01', 59, 1), {'run': 1}) == ['2026-01', '2026-02']
    # A later run covers February and March only
    assert storage.write_processed(1, 'long', frame('2026-02-01', 40, 2), {'run': 2}) == ['2026-02', '2026-03']

    data = read(root, 1, 'long')
    assert data.loc['2026-01', 'CO2'].unique().tolist() == [1.0]
    assert data.loc['2026-02':, 'CO2'].unique().tolist() == [2.0]
    # January from the first run, then the 40 days of the second
    assert len(data) == (31 + 40) * 24 and data.index.is_unique
    assert storage.read_run_info(1, 'long')['run'] == 2


def test_month_start():
    assert storage.month_start('2026-10-08T13:00:00+00:00') == pd.Timestamp('2026-10-01', tz='UTC')


# Task

class FakeDevice:
    ''' scdata.Device replacement for long processing '''
    options = {}

    def __init__(self, params, **kwargs):
        hardware = FakeDevice.options.get('hardware', 'SCAS_TEST1')
        self.handler = SimpleNamespace(json=SimpleNamespace(
            name='Kit', postprocessing=SimpleNamespace(hardware_url=f'https://flows/api/v1/hardware/{hardware}.json')))
        self.blueprint = 'test_air'
        self.checks = ['GAPS']
        self.channels = []
        self.required_sensors = ['SCD30_CO2']
        self.data = pd.DataFrame()
        self.health = {}
        FakeDevice.instance = self

    def use_blueprint(self, name, blueprint):
        self.blueprint = name
        self.channels = [SimpleNamespace(name=channel['name'], kwargs=channel.get('kwargs', {}))
                         for channel in blueprint['channels']]
        return True

    def load_from_storage(self, **kwargs):
        self.loaded_with = kwargs
        if FakeDevice.options.get('empty'):
            return False
        self.data = frame('2026-08-01', 3, 400).rename(columns={'CO2': 'SCD30_CO2'})
        return True

    def process(self):
        self.data['CO2'] = self.data['SCD30_CO2'] - 10
        return FakeDevice.options.get('process', True)

    def health_checks(self):
        self.health = {'start': None, 'end': None, 'rows': len(self.data),
                       'checks': [{'name': 'GAPS', 'status': 'ok', 'columns': {}}]}


@pytest.fixture
def long_hardware(app):
    ''' SCAS_TEST1 with test_air (process) and test_long (long) '''
    import_metadata(DATA)
    body = editing.find('blueprint', 'test_air').body
    channels = [{'name': 'CO2', 'function': 'baseline_als', 'kwargs': {'name': 'SCD30_CO2', 'lam': 1e10}}]
    editing.save_blueprint('test_long', dict(body, meta={'kind': 'long', 'window_days': 90, 'every_days': 7},
                                             channels=channels))
    hardware = editing.find('hardware', 'SCAS_TEST1')
    hardware.blueprints = [hardware.blueprint, editing.find('blueprint', 'test_long')]
    db.session.commit()


@pytest.fixture
def run(monkeypatch, root, long_hardware):
    backups = []

    async def back_up(device_id, logger_handler, task_log):
        backups.append(device_id)
        return FakeDevice.options.get('backup', True)

    monkeypatch.setattr(dlong_module.sc, 'Device', FakeDevice)
    monkeypatch.setattr(dlong_module, 'refresh_metadata', lambda: None)
    monkeypatch.setattr(dlong_module, 'back_up', back_up)

    def run_dlong(dry_run=False, **options):
        FakeDevice.options = options
        log, state, health = asyncio.run(dlong(1, dry_run=dry_run))
        return state, FakeDevice.instance if hasattr(FakeDevice, 'instance') else None, health, log

    run_dlong.backups = backups
    return run_dlong


def test_long_run_stores_the_result(run, root):
    state, device, health, log = run()

    assert state == ['SUCCESS', 'PROCESSED AND STORED']
    assert run.backups == [1]
    assert device.blueprint == 'test_long'
    # The window starts on the first day of the month, 90 days back at least
    start = device.loaded_with['min_date']
    assert start.day == 1 and datetime.now(timezone.utc) - start >= timedelta(days=90)
    assert device.loaded_with['channels'] == ['SCD30_CO2']
    assert read(root, 1, 'test_long')['CO2'].unique().tolist() == [390.0]
    info = storage.read_run_info(1, 'test_long')
    assert info['channels'] == ['CO2'] and info['parameters'] == {'CO2': {'name': 'SCD30_CO2', 'lam': 1e10}}
    assert health['blueprint'] == 'test_long' and health['device_name'] == 'Kit'


def test_dry_run_stores_nothing(run, root):
    state, _, health, _ = run(dry_run=True)

    assert state == ['SUCCESS', 'PROCESSED (DRY RUN)'] and health is not None
    assert not (root / 'devices').exists()


@pytest.mark.parametrize('options, state', [
    ({'backup': False}, ['ABORTED', 'BACKUP_FAILED']),
    ({'hardware': 'SCAS_TEST2'}, ['ABORTED', 'NO_LONG_BLUEPRINT']),
    ({'empty': True}, ['ABORTED', 'EMPTY_DATA']),
    ({'process': False}, ['ABORTED', 'PROCESSING_FAILED']),
])
def test_long_run_aborts(run, root, options, state):
    assert run(**options)[0] == state
    assert not (root / 'devices').exists()


def test_back_up_skips_when_a_backup_is_running(app, monkeypatch):
    monkeypatch.delenv('STORAGE_ROOT', raising=False)
    monkeypatch.setenv('S3_DATA_BUCKET', 'bucket')
    monkeypatch.setattr(dlong_module.locks, 'acquire', lambda key, seconds=None: None)
    log = []

    assert asyncio.run(dlong_module.back_up(1, lambda message, level='info': message, log)) is True
    assert 'A backup of the device is running' in log[0]


# Jobs

def test_sync_creates_long_jobs(app):
    jobs.sync_jobs(to_process={1}, to_back_up={1}, for_long={1: 48})

    long_job = db.session.execute(db.select(Job).filter_by(task='long', device_id=1)).scalar_one()
    assert long_job.interval_hours == 48

    jobs.sync_jobs(to_process={1}, to_back_up={1}, for_long={})
    db.session.expire_all()
    assert not db.session.get(Job, long_job.id).enabled


def test_devices_for_long(app, long_hardware, monkeypatch):
    import smartcitizen_connector

    devices = pd.DataFrame([
        {'id': 1, 'last_reading_at': '2026-10-01T00:00:00Z', 'postprocessing': {'hardware_url': 'SCAS_TEST1'}},
        {'id': 2, 'last_reading_at': '2026-10-01T00:00:00Z', 'postprocessing': {'hardware_url': 'SCAS_TEST2'}},
        {'id': 3, 'last_reading_at': None, 'postprocessing': {'hardware_url': 'SCAS_TEST1'}},
    ]).set_index('id')
    monkeypatch.setattr(smartcitizen_connector, 'search_by_query', lambda **kwargs: devices)

    assert jobs.devices_for_long() == {1: 7 * 24}


def test_back_up_is_skipped_with_local_storage(app, root):
    log = []

    assert asyncio.run(dlong_module.back_up(1, lambda message, level='info': message, log)) is True
    assert 'using the backups as they are' in log[0]


def test_backups_are_read_from_the_storage_root(run, root):
    _, device, _, _ = run()

    assert device.loaded_with['root'] == str(root)
