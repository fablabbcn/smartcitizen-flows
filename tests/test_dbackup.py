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

    def backup_to_storage(self, mode):
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
