from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from flask import g

import scflows.jobs as jobs
import scflows.worker as worker
from scflows import db
from scflows.models import Job, JobRun

from conftest import USERS, auth


@pytest.fixture
def queued(monkeypatch):
    ''' Runs sent to the workers '''
    sent = []
    monkeypatch.setattr(worker.run_job, 'delay', lambda run_id: sent.append(run_id))
    return sent


@pytest.fixture
def lock(monkeypatch):
    ''' In memory locks instead of Redis '''
    held = {}

    def acquire(key, seconds=None):
        if key in held:
            return None
        held[key] = 'token'
        return 'token'

    monkeypatch.setattr(jobs.locks, 'acquire', acquire)
    monkeypatch.setattr(jobs.locks, 'release', lambda key, token: held.pop(key, None))
    return held


@pytest.fixture
def task(monkeypatch):
    ''' Replaces dprocess and dbackup '''
    calls = []
    result = {'state': ['SUCCESS', 'PROCESSED AND UPLOADED'], 'error': None}

    def task_function(name):
        async def run(device_id, dry_run):
            calls.append((name, device_id, dry_run))
            if result['error']:
                raise result['error']
            return ['info: done'], result['state']
        return run

    monkeypatch.setattr(jobs, 'task_function', task_function)
    return SimpleNamespace(calls=calls, result=result)


def job(task='process', device_id=1, **values):
    return db.session.execute(db.select(Job).filter_by(task=task, device_id=device_id)).scalar_one()


def make_run(device_id=1, task='process', **values):
    run = JobRun(device_id=device_id, task=task, **values)
    db.session.add(run)
    db.session.commit()
    return run


# Sync

def test_sync_creates_spread_jobs(app):
    before = jobs.now()

    assert jobs.sync_jobs(to_process={1, 2}, to_back_up={3}) == {
        'process': {'created': 2, 'enabled': 0, 'disabled': 0},
        'backup': {'created': 1, 'enabled': 0, 'disabled': 0}}

    process = job('process', 1)
    assert process.enabled and not process.paused and process.source == 'auto'
    assert process.interval_hours == 3
    assert before <= jobs.aware(process.next_run_at) <= before + timedelta(hours=3)
    assert job('backup', 3).interval_hours == 6


def test_sync_disables_and_enables(app):
    jobs.sync_jobs(to_process={1, 2}, to_back_up=set())

    assert jobs.sync_jobs(to_process={1}, to_back_up=set())['process'] == {'created': 0, 'enabled': 0, 'disabled': 1}
    assert not job('process', 2).enabled
    assert jobs.sync_jobs(to_process={1, 2}, to_back_up=set())['process'] == {'created': 0, 'enabled': 1, 'disabled': 0}
    assert job('process', 2).enabled


def test_sync_keeps_paused_and_manual_jobs(app):
    jobs.sync_jobs(to_process={1, 2}, to_back_up=set())
    job('process', 1).paused = True
    job('process', 2).source = Job.MANUAL
    db.session.commit()

    jobs.sync_jobs(to_process={1}, to_back_up=set())

    assert job('process', 1).paused and job('process', 1).enabled
    assert job('process', 2).enabled


def test_devices_to_process(monkeypatch):
    import smartcitizen_connector
    import smartcitizen_connector.device

    devices = pd.DataFrame([
        {'id': 1, 'last_reading_at': '2026-10-01T00:00:00Z',
         'postprocessing': {'hardware_url': 'SCAS1', 'latest_postprocessing': '2026-09-01T00:00:00Z'}},
        # Already processed
        {'id': 2, 'last_reading_at': '2026-10-01T00:00:00Z',
         'postprocessing': {'hardware_url': 'SCAS2', 'latest_postprocessing': '2026-10-02T00:00:00Z'}},
        # No hardware
        {'id': 3, 'last_reading_at': '2026-10-01T00:00:00Z', 'postprocessing': {'hardware_url': ''}},
        # No readings
        {'id': 4, 'last_reading_at': None, 'postprocessing': {'hardware_url': 'SCAS4'}},
        # Invalid hardware
        {'id': 5, 'last_reading_at': '2026-10-01T00:00:00Z', 'postprocessing': {'hardware_url': 'BAD'}},
        # Never processed
        {'id': 6, 'last_reading_at': '2026-10-01T00:00:00Z',
         'postprocessing': {'hardware_url': 'SCAS6', 'latest_postprocessing': None}},
    ]).set_index('id')
    checked = []

    def check_postprocessing(postprocessing):
        checked.append(postprocessing['hardware_url'])
        return None, None, postprocessing['hardware_url'] != 'BAD'

    monkeypatch.setattr(smartcitizen_connector, 'search_by_query', lambda **kwargs: devices)
    monkeypatch.setattr(smartcitizen_connector.device, 'check_postprocessing', check_postprocessing)

    assert jobs.devices_to_process() == {1, 6}
    assert checked == ['SCAS1', 'BAD', 'SCAS6']


# Dispatch

def test_dispatch_due_jobs(app, queued):
    jobs.sync_jobs(to_process={1, 2, 3, 4}, to_back_up=set())
    past = jobs.now() - timedelta(minutes=10)
    for device_id in (1, 2, 3):
        job('process', device_id).next_run_at = past
    job('process', 2).paused = True
    job('process', 3).enabled = False
    job('process', 4).next_run_at = jobs.now() + timedelta(hours=1)
    db.session.commit()

    runs = jobs.dispatch_due_jobs()

    assert [(run.device_id, run.state) for run in runs] == [(1, 'queued')]
    assert queued == [runs[0].id]
    assert jobs.aware(job('process', 1).next_run_at) == past + timedelta(hours=3)
    assert jobs.dispatch_due_jobs() == []


def test_next_run_keeps_the_minute_after_downtime(app):
    item = SimpleNamespace(next_run_at=datetime(2026, 10, 1, 0, 17, tzinfo=timezone.utc), interval_hours=3)

    assert jobs.next_run(item, datetime(2026, 10, 1, 0, 20, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 1, 3, 17, tzinfo=timezone.utc)
    # Missed runs (e.g. beat stopped for a day) are not queued again
    assert jobs.next_run(item, datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 2, 3, 17, tzinfo=timezone.utc)


# Runs

def test_execute_run(app, lock, task):
    run = make_run(dry_run=True)

    run = jobs.execute_run(run.id)

    assert (run.state, run.message, run.log) == ('success', 'PROCESSED AND UPLOADED', ['info: done'])
    assert run.started_at and run.finished_at
    assert task.calls == [('process', 1, True)]
    assert lock == {}


@pytest.mark.parametrize('state, expected', [
    (['FAILED', 'DATA_POSTING_FAILED'], 'failed'),
    (['ABORTED', 'EMPTY_DATA'], 'aborted'),
])
def test_execute_run_results(app, lock, task, state, expected):
    task.result['state'] = state

    run = jobs.execute_run(make_run().id)

    assert (run.state, run.message) == (expected, state[1])


def test_execute_run_error(app, lock, task):
    task.result['error'] = RuntimeError('boom')

    run = jobs.execute_run(make_run().id)

    assert (run.state, run.message) == ('failed', 'RuntimeError: boom')
    assert lock == {}


def test_execute_run_already_running(app, lock, task):
    lock['scflows:lock:process:1'] = 'other'

    run = jobs.execute_run(make_run().id)

    assert (run.state, run.message) == ('aborted', 'ALREADY_RUNNING')
    assert task.calls == []


def test_execute_run_only_once(app, lock, task):
    run = make_run()
    jobs.execute_run(run.id)

    jobs.execute_run(run.id)

    assert len(task.calls) == 1


def test_celery_task_runs_in_app_context(app, lock, task, monkeypatch):
    monkeypatch.setattr(worker, '_flask_app', app)
    run = make_run(task='backup')

    worker.run_job(run.id)

    db.session.expire_all()
    assert db.session.get(JobRun, run.id).state == 'success'
    assert task.calls == [('backup', 1, False)]


# Command line, API and interface

def test_cli(app, lock, task, queued):
    runner = app.test_cli_runner()
    jobs.sync_jobs(to_process={1}, to_back_up={2})

    assert 'process       1 active' in runner.invoke(args=['jobs', 'list']).output
    assert 'Run 1: success PROCESSED AND UPLOADED' in runner.invoke(
        args=['jobs', 'run', '1', 'process', '--dry-run', '--inline']).output
    assert 'Run 2 queued' in runner.invoke(args=['jobs', 'run', '2', 'backup']).output
    assert queued == [2]


def test_jobs_api_requires_admin(app, client, sc_me):
    jobs.sync_jobs(to_process={1}, to_back_up=set())
    make_run(job=job('process', 1), state='success', log=['info: done'])

    assert client.get('/api/v1/jobs').status_code == 401
    assert client.get('/api/v1/jobs', headers=auth('researcher-token')).status_code == 403

    data = client.get('/api/v1/jobs?task=process', headers=auth('admin-token')).get_json()
    assert [(item['device_id'], item['task']) for item in data] == [(1, 'process')]
    runs = client.get(f"/api/v1/jobs/{data[0]['id']}/runs", headers=auth('admin-token')).get_json()
    assert [item['state'] for item in runs] == ['success']
    assert client.get(f"/api/v1/runs/{runs[0]['id']}", headers=auth('admin-token')).get_json()['log'] == ['info: done']


def sign_in(client, role):
    user = next(user for user in USERS.values() if user['role'] == role)
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user)
        session['_user_id'] = str(user['id'])


def test_jobs_page_requires_admin(app, client):
    jobs.sync_jobs(to_process={1}, to_back_up={2})
    run = make_run(job=job('process', 1), state='success', message='PROCESSED AND UPLOADED', log=['info: done'])

    sign_in(client, 'researcher')
    assert client.get('/jobs/').status_code == 403

    sign_in(client, 'admin')
    page = client.get('/jobs/').get_data(as_text=True)
    assert 'kits/1' in page and 'kits/2' in page and 'PROCESSED AND UPLOADED' in page
    assert 'info: done' in client.get(f'/jobs/runs/{run.id}').get_data(as_text=True)


def test_run_page_shows_log_levels(app, client):
    run = make_run(state='failed', log=['info: loaded', 'warning: no data', 'error: not posted', 'plain line'])

    sign_in(client, 'admin')
    page = client.get(f'/jobs/runs/{run.id}').get_data(as_text=True)

    assert '<div class="log-line warning" data-text="warning: no data">' in page
    assert '<span class="log-message">not posted</span>' in page
    assert '<div class="log-line " data-text="plain line">' in page


def test_overview_figures(app, client):
    jobs.sync_jobs(to_process={1, 2}, to_back_up={3})
    job('process', 1).paused = True
    make_run(state='failed')

    sign_in(client, 'admin')
    page = client.get('/').get_data(as_text=True)
    assert '2 active' in page and '1 paused' in page and '1 failed in 24 h' in page

    sign_in(client, 'researcher')
    page = client.get('/').get_data(as_text=True)
    assert 'active' not in page and 'hardware' in page
