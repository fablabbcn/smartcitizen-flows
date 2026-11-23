''' Reprocessing a device: requested by an admin or when its processing changes, run after run to the last reading '''
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import scflows.jobs as jobs
import scflows.tasks.dprocess as dprocess_module
from scflows import db, editing
from scflows.config import config
from scflows.metadata import import_metadata
from scflows.models import Job, JobRun

from conftest import DATA, auth
from test_devices import page, sign_in
from test_dprocess import FakeDevice
from test_jobs import lock, task


@pytest.fixture
def client(app, sc_me):
    import_metadata(DATA)
    jobs.sync_jobs(to_process={1}, to_back_up=set())
    jobs.find_job(1, 'process').hardware = 'SCAS_TEST1'
    db.session.commit()
    return app.test_client()


def posted(parameters, blueprint='test_air'):
    ''' A run of device 1 that posted, with these process parameters '''
    db.session.add(JobRun(job=jobs.find_job(1, 'process'), device_id=1, task='process', state='success',
                          context={'blueprint': blueprint, 'process_parameters': parameters}))
    db.session.commit()


def process_parameters(parameters):
    hardware = editing.find('hardware', 'SCAS_TEST1').to_json()
    hardware['process_parameters'] = parameters
    return hardware


def test_runs_follow_each_other_until_the_last_reading(client, lock, task):
    job = jobs.find_job(1, 'process')
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    jobs.request_reprocess(job, start)
    db.session.commit()
    assert job.reprocess == 'pending' and jobs.aware(job.next_run_at) <= jobs.now()

    received = []
    original = jobs.task_function

    def task_function(name):
        run = original(name)

        async def recorded(device_id, dry_run, context):
            received.append(dict(context))
            return await run(device_id, dry_run, context)
        return recorded

    jobs.task_function = task_function
    try:
        # A dry run changes nothing
        task.result['context'] = {'behind': False}
        dry = JobRun(job=job, device_id=1, task='process', dry_run=True)
        db.session.add(dry)
        db.session.commit()
        jobs.execute_run(dry.id)
        assert job.reprocess == 'pending'

        task.result['context'] = {'behind': True}
        first = JobRun(job=job, device_id=1, task='process')
        db.session.add(first)
        db.session.commit()
        jobs.execute_run(first.id)
        assert received[-1]['reprocess'] == 'pending' and received[-1]['reprocess_from'] == start.isoformat()
        assert job.reprocess == 'running' and jobs.aware(job.next_run_at) <= jobs.now() + timedelta(minutes=1)

        task.result['context'] = {'behind': False}
        second = JobRun(job=job, device_id=1, task='process')
        db.session.add(second)
        db.session.commit()
        jobs.execute_run(second.id)
        assert received[-1]['reprocess'] == 'running'
        assert job.reprocess is None and job.reprocess_from is None
    finally:
        jobs.task_function = original


def test_the_first_run_starts_again_and_loads_more(client, monkeypatch):
    monkeypatch.setattr(dprocess_module.sc, 'Device', FakeDevice)
    monkeypatch.setenv('SC_BEARER', 'test-token')
    monkeypatch.setattr(dprocess_module, 'refresh_metadata', lambda: None)
    FakeDevice.options = {}

    context = {'reprocess': 'pending', 'reprocess_from': None}
    log, state, _ = asyncio.run(dprocess_module.dprocess(1, dry_run=True, context=context))

    assert FakeDevice.instance.options.min_date is None
    assert FakeDevice.instance.options.limit == config._reprocess_load_amount
    assert any('Reprocessing from the first hardware version' in line for line in log)


def test_changed_parameters_are_listed_or_reprocessed(client):
    posted({})
    headers = auth('admin-token')

    response = client.put('/api/v1/hardware/SCAS_TEST1', json=process_parameters({'NO2': {'lookback_hours': 24}}),
                          headers=headers)
    assert response.status_code == 200
    assert any(warning.startswith('Devices 1 have data posted with other processing of: NO2')
               for warning in response.get_json()['warnings'])
    assert jobs.find_job(1, 'process').reprocess is None

    response = client.put('/api/v1/hardware/SCAS_TEST1?reprocess=true',
                          json=process_parameters({'NO2': {'lookback_hours': 24}}), headers=headers)
    assert response.get_json()['reprocess'] == [1] and jobs.find_job(1, 'process').reprocess == 'pending'


def test_devices_posted_with_the_same_processing_are_not_listed(client):
    posted({'NO2': {'lookback_hours': 24}})

    response = client.put('/api/v1/hardware/SCAS_TEST1', json=process_parameters({'NO2': {'lookback_hours': 24}}),
                          headers=auth('admin-token'))

    assert not [warning for warning in response.get_json()['warnings'] if warning.startswith('Devices')]


def test_device_page_warns_and_reprocesses(client):
    posted({'NO2': {'lookback_hours': 24}})
    sign_in(client, 'admin')

    text = page(client, '/jobs/devices/1')
    assert 'processed with other parameters than its hardware has now, for: NO2' in text
    assert 'cannot delete readings' in text

    job = jobs.find_job(1, 'process')
    response = client.post(f'/jobs/{job.id}/reprocess', data={'csrf': 'token', 'from': '2026-03-01'})
    assert response.status_code == 302
    assert job.reprocess == 'pending' and job.reprocess_from.date().isoformat() == '2026-03-01'


def test_reprocess_api(client):
    job = jobs.find_job(1, 'process')

    response = client.post(f'/api/v1/jobs/{job.id}/reprocess', json={}, headers=auth('admin-token'))

    assert response.status_code == 200 and response.get_json()['reprocess'] == 'pending'
    assert 'cannot delete readings' in response.get_json()['warnings'][0]
    backup = Job(device_id=1, task='backup', interval_hours=6, next_run_at=jobs.now())
    db.session.add(backup)
    db.session.commit()
    assert client.post(f'/api/v1/jobs/{backup.id}/reprocess', json={}, headers=auth('admin-token')).status_code == 400
