import asyncio
import datetime
import json
from types import SimpleNamespace

import botocore.exceptions
import pandas as pd
import pytest

import scflows.tasks.dbackup as dbackup_module


class FakeObject:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self):
        if self.key not in self.store:
            raise botocore.exceptions.ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': SimpleNamespace(read=lambda: self.store[self.key])}

    def put(self, Body):
        self.store[self.key] = Body


class FakeDevice:
    def __init__(self, created_at, last_reading_at):
        self.id = 1
        self.options = SimpleNamespace(min_date=None, max_date=None)
        self.handler = SimpleNamespace(json=SimpleNamespace(created_at=created_at, last_reading_at=last_reading_at))
        self.data = pd.DataFrame()
        self.loaded = False
        # Hardware versions: scdata does not load the readings before the first one
        self.versions = [{'from_date': created_at + datetime.timedelta(days=2)}]
        FakeDevice.instance = self

    def __bool__(self):
        return True

    async def load(self):
        self.data = pd.DataFrame({'TEMP': [20.0]}, index=pd.DatetimeIndex([self.options.max_date]))
        self.loaded = True
        return True

    def backup_to_storage(self, mode, root=None):
        self.backed_up = (mode, root)
        return True


@pytest.fixture
def s3(monkeypatch):
    store = {}
    monkeypatch.setenv('S3_DATA_BUCKET', 'bucket')
    monkeypatch.setattr(dbackup_module, 'refresh_metadata', lambda: None)
    monkeypatch.setattr(dbackup_module.boto3, 'resource',
                        lambda name: SimpleNamespace(Object=lambda bucket, key: FakeObject(store, key)))
    return store


def test_first_backup_stops_at_the_last_reading(s3, monkeypatch):
    utc = datetime.timezone.utc
    created = datetime.datetime(2026, 10, 1, tzinfo=utc)
    last = datetime.datetime(2026, 10, 5, tzinfo=utc)
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(created, last))

    _, state = asyncio.run(dbackup_module.dbackup(1))

    assert state == ['SUCCESS', 'BACKUP_DONE']
    stored = json.loads(s3['devices/1/request.json'])
    assert datetime.datetime.fromisoformat(stored['last_requested_data']) == last


def stored_backup(s3, until):
    s3['devices/1/request.json'] = json.dumps({'last_requested_data': until.isoformat()}).encode()


def test_backups_wait_for_their_period_to_end(s3, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    stored_backup(s3, now - datetime.timedelta(days=5))
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(now - datetime.timedelta(days=100), now))

    _, state = asyncio.run(dbackup_module.dbackup(1))

    assert state == ['ABORTED', 'NO_NEW_DATA']


def test_up_to_date_backups_reach_the_last_reading(s3, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    last = now - datetime.timedelta(hours=1)
    stored_backup(s3, now - datetime.timedelta(days=5))
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(now - datetime.timedelta(days=100), last))

    _, state = asyncio.run(dbackup_module.dbackup(1, up_to_date=True))

    assert state == ['SUCCESS', 'BACKUP_DONE']
    assert datetime.datetime.fromisoformat(json.loads(s3['devices/1/request.json'])['last_requested_data']) == last


def test_whole_periods_are_backed_up(s3, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=50)
    stored_backup(s3, start)
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(now - datetime.timedelta(days=100), now))

    _, state = asyncio.run(dbackup_module.dbackup(1))

    assert state == ['SUCCESS', 'BACKUP_DONE']
    until = datetime.datetime.fromisoformat(json.loads(s3['devices/1/request.json'])['last_requested_data'])
    assert until == start + datetime.timedelta(days=dbackup_module.config._backup_interval_days)


def test_backups_keep_the_readings_before_the_first_hardware_version(s3, monkeypatch):
    utc = datetime.timezone.utc
    created = datetime.datetime(2026, 10, 1, tzinfo=utc)
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(created, created + datetime.timedelta(days=5)))

    _, state = asyncio.run(dbackup_module.dbackup(1))

    assert state == ['SUCCESS', 'BACKUP_DONE']
    assert FakeDevice.instance.versions == []


def test_backups_to_a_local_storage(monkeypatch, tmp_path):
    # A local stack without S3 (STORAGE_ROOT): request.json and the backups go to the folder
    utc = datetime.timezone.utc
    monkeypatch.setenv('STORAGE_ROOT', str(tmp_path))
    monkeypatch.delenv('S3_DATA_BUCKET', raising=False)
    monkeypatch.setattr(dbackup_module, 'refresh_metadata', lambda: None)
    monkeypatch.setattr(dbackup_module.boto3, 'resource', lambda name: pytest.fail('S3 used'))
    created, last = datetime.datetime(2026, 10, 1, tzinfo=utc), datetime.datetime(2026, 10, 5, tzinfo=utc)
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(created, last))

    context = {}
    _, state = asyncio.run(dbackup_module.dbackup(1, context=context))

    assert state == ['SUCCESS', 'BACKUP_DONE']
    assert FakeDevice.instance.backed_up == ('overwrite', str(tmp_path))
    assert context['storage'] == f'{tmp_path}/devices/1/'
    stored = json.loads((tmp_path / 'devices' / '1' / 'request.json').read_text())
    assert datetime.datetime.fromisoformat(stored['last_requested_data']) == last

    # The next backup continues from there
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: FakeDevice(created, last))
    _, state = asyncio.run(dbackup_module.dbackup(1, up_to_date=True))
    assert state == ['ABORTED', 'NO_NEW_DATA']


def test_backups_to_a_local_storage_with_scdata(monkeypatch, tmp_path):
    # The backup of scdata itself, read back as from S3
    from scdata.device.device import Device, read_storage

    class ScdataDevice(FakeDevice):
        def backup_to_storage(self, mode, root=None):
            self.handler.json.model_dump_json = lambda: '{"id": 1}'
            self._backup_to_folder = lambda *args: Device._backup_to_folder(self, *args)
            return Device.backup_to_storage(self, mode=mode, root=root)

    utc = datetime.timezone.utc
    monkeypatch.setenv('STORAGE_ROOT', str(tmp_path))
    monkeypatch.delenv('S3_DATA_BUCKET', raising=False)
    monkeypatch.setattr(dbackup_module, 'refresh_metadata', lambda: None)
    monkeypatch.setattr(dbackup_module.boto3, 'resource', lambda name: pytest.fail('S3 used'))
    created, last = datetime.datetime(2026, 10, 1, tzinfo=utc), datetime.datetime(2026, 10, 5, tzinfo=utc)
    monkeypatch.setattr(dbackup_module.sc, 'Device', lambda **kwargs: ScdataDevice(created, last))

    _, state = asyncio.run(dbackup_module.dbackup(1))

    assert state == ['SUCCESS', 'BACKUP_DONE']
    data = read_storage(str(tmp_path / 'devices' / '1' / 'data'))
    assert list(data.columns) == ['TEMP'] and data.index[0] == last
    assert (tmp_path / 'devices' / '1' / 'metadata.json').read_text() == '{"id": 1}'


def test_a_failed_write_keeps_the_previous_request(tmp_path, monkeypatch):
    path = tmp_path / 'devices' / '1' / 'request.json'
    dbackup_module.LocalObject(str(path)).put(b'{"last_requested_data": "2026-10-05T00:00:00+00:00"}')

    def broken(descriptor, mode):
        raise OSError('disk full')

    monkeypatch.setattr(dbackup_module.os, 'fdopen', broken)
    with pytest.raises(OSError):
        dbackup_module.LocalObject(str(path)).put(b'{"last_requested_data": "2026-11-01T00:00:00+00:00"}')

    assert json.loads(path.read_text())['last_requested_data'] == '2026-10-05T00:00:00+00:00'
    assert [item.name for item in path.parent.iterdir()] == ['request.json']
