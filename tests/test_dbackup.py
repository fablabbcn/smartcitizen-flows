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
