''' Devices, hardware and calibrations linked both ways; what each run used '''
import asyncio
import logging

import pandas as pd
import pytest
from flask import g

import scflows.jobs as jobs
from scflows import db
from scflows.metadata import import_metadata
from scflows.models import Job, JobRun
from scflows.tasks.context import library_warnings

from conftest import DATA, USERS


@pytest.fixture
def client(app):
    import_metadata(DATA)
    jobs.sync_jobs(to_process={1, 2}, to_back_up={1})
    for job in db.session.execute(db.select(Job)).scalars():
        job.device_name = {1: 'Rooftop', 2: 'Balcony'}[job.device_id]
        job.hardware = {1: 'SCAS_TEST1', 2: 'SCAS_TEST2'}[job.device_id]
    db.session.commit()
    return app.test_client()


def sign_in(client, role, hardware=(), devices=()):
    user = dict(next(user for user in USERS.values() if user['role'] == role), hardware=list(hardware), devices=list(devices))
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = user
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def page(client, path, status=200):
    response = client.get(path)
    assert response.status_code == status, path
    return response.get_data(as_text=True)


def test_jobs_table_links_devices_and_hardware(client):
    sign_in(client, 'admin')
    text = page(client, '/jobs/')

    assert 'href="/jobs/devices/1"' in text and 'href="/metadata/hardware/SCAS_TEST1"' in text
    # SCAS_TEST2 mounts 202760040, without calibration
    assert '1 uncalibrated' in text and '202760040' in text


def test_device_page(client):
    run = JobRun(job=jobs.find_job(2, 'process'), device_id=2, task='process', state='failed', message='DATA_POSTING_FAILED')
    db.session.add(run)
    db.session.commit()
    sign_in(client, 'admin')

    text = page(client, '/jobs/devices/2')

    assert 'Balcony' in text and 'href="/metadata/hardware/SCAS_TEST2"' in text
    assert 'href="/metadata/calibrations/212830246"' in text
    # Without calibration: a link to add it
    assert 'href="/metadata/calibrations/new?sensor_id=202760040"' in text and 'no calibration' in text
    assert f'href="/jobs/runs/{run.id}"' in text and 'DATA_POSTING_FAILED' in text

    sign_in(client, 'researcher', hardware=['SCAS_TEST2'], devices=[2])
    page(client, '/jobs/devices/2', 403)


def test_hardware_page_lists_its_devices(client):
    sign_in(client, 'admin')
    text = page(client, '/metadata/hardware/SCAS_TEST2')
    assert 'href="/jobs/devices/2"' in text and 'Balcony' in text and 'Rooftop' not in text
    assert 'without calibration' in text and '202760040' in text

    # Researchers see their own devices, linked to their health
    sign_in(client, 'researcher', hardware=['SCAS_TEST1', 'SCAS_TEST2'], devices=[])
    text = page(client, '/metadata/hardware/SCAS_TEST2')
    assert 'Balcony' not in text and 'that you own' in text
    sign_in(client, 'researcher', hardware=['SCAS_TEST2'], devices=[2])
    assert 'href="/health/2"' in page(client, '/metadata/hardware/SCAS_TEST2')


def test_calibration_page_lists_hardware_and_devices(client):
    sign_in(client, 'admin')
    text = page(client, '/metadata/calibrations/212830246')

    # Mounted by both: SCAS_TEST1 in AS_48_32, SCAS_TEST2 in AS_48_01 since 2024
    assert 'href="/metadata/hardware/SCAS_TEST1"' in text and 'href="/metadata/hardware/SCAS_TEST2"' in text
    assert 'AS_48_32' in text and 'AS_48_01' in text
    assert 'href="/jobs/devices/1"' in text and 'href="/jobs/devices/2"' in text

    sign_in(client, 'researcher', hardware=['SCAS_TEST1'], devices=[1])
    text = page(client, '/metadata/calibrations/212830246')
    assert 'SCAS_TEST2' not in text and 'Balcony' not in text and 'Rooftop' in text


def test_run_page_shows_what_the_run_used(client):
    context = {'device_name': 'Balcony', 'hardware': 'SCAS_TEST2', 'blueprints': ['test_air'],
               'versions': [{'ids': {'AS_48_01': '202760040'}, 'from': '2023-01-01', 'to': None}],
               'uncalibrated': ['202760040'], 'storage': 's3://bucket/devices/2/',
               'min_date': '2026-10-01T00:00:00+00:00', 'max_date': '2026-10-05T00:00:00+00:00', 'rows': 10}
    run = JobRun(device_id=2, task='backup', state='success', message='BACKUP_DONE', context=context)
    db.session.add(run)
    db.session.commit()
    sign_in(client, 'admin')

    text = page(client, f'/jobs/runs/{run.id}')

    assert 'What the run used' in text and 'href="/metadata/hardware/SCAS_TEST2"' in text
    assert 'href="/jobs/devices/2"' in text and 'no calibration' in text
    assert 'https://s3.console.aws.amazon.com/s3/buckets/bucket?prefix=devices/2/' in text


def test_execute_run_stores_the_context(app, monkeypatch):
    monkeypatch.setattr(jobs.locks, 'acquire', lambda key, seconds=None: 'token')
    monkeypatch.setattr(jobs.locks, 'release', lambda key, token: None)

    def task_function(name):
        async def run(device_id, dry_run, context):
            context.update(hardware='SCAS_TEST1', rows=3)
            return ['info: done'], ['SUCCESS', 'BACKUP_DONE']
        return run

    monkeypatch.setattr(jobs, 'task_function', task_function)
    run = JobRun(device_id=7, task='backup')
    db.session.add(run)
    db.session.commit()

    jobs.execute_run(run.id)

    db.session.expire_all()
    assert db.session.get(JobRun, run.id).context == {'hardware': 'SCAS_TEST1', 'rows': 3}


def test_sync_stores_the_hardware(app, monkeypatch):
    import smartcitizen_connector
    import smartcitizen_connector.device

    devices = pd.DataFrame([{'id': 1, 'name': 'Rooftop', 'last_reading_at': '2026-10-01T00:00:00Z',
                             'postprocessing': {'hardware_url': 'https://flows/api/v1/hardware/LONG.json',
                                                'latest_postprocessing': None}}]).set_index('id')
    monkeypatch.setattr(smartcitizen_connector, 'search_by_query', lambda **kwargs: devices)
    monkeypatch.setattr(smartcitizen_connector.device, 'check_postprocessing', lambda postprocessing: (None, None, True))
    monkeypatch.setattr(jobs, 'hardware_kinds', lambda: {'LONG': {'process', 'long'}})

    jobs.sync_jobs()

    assert {job.hardware for job in db.session.execute(db.select(Job)).scalars()} == {'LONG'}


def test_library_warnings_go_to_the_innermost_task():
    outer, inner = [], []
    with library_warnings(outer):
        logging.getLogger('smartcitizen_connector').warning('outer warning')
        with library_warnings(inner):
            logging.getLogger('scdata').error('cannot post')
            logging.getLogger('scdata').info('not kept')

    assert outer == ['warning: [smartcitizen_connector] outer warning']
    assert inner == ['error: [scdata] cannot post']


def test_repeated_warnings_are_counted():
    task_log = []
    with library_warnings(task_log):
        for _ in range(3):
            logging.getLogger('scdata').warning('Requested channel is None')
            logging.getLogger('scdata').warning('WARNING_NULL_CHANNEL')
        task_log.append('info: done')

    assert task_log == ['warning: [scdata] Requested channel is None (3 times)',
                        'warning: [scdata] WARNING_NULL_CHANNEL (3 times)', 'info: done']
