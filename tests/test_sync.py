import pytest
from flask import g

import scflows.jobs as jobs
import scflows.worker as worker
from scflows import db
from scflows.models import JobRun

from conftest import USERS, auth


@pytest.fixture
def report(monkeypatch):
    result = {'process': {'created': 2, 'enabled': 0, 'disabled': 1}}

    def sync_jobs(details=None):
        jobs.logger.info('120 devices with postprocessing')
        return result
    monkeypatch.setattr(jobs, 'sync_jobs', sync_jobs)
    return result


@pytest.fixture
def queued(monkeypatch):
    ''' Sync runs sent to the workers '''
    sent = []
    monkeypatch.setattr(worker.sync_jobs, 'delay', lambda run_id: sent.append(run_id))
    return sent


def sync_runs():
    db.session.expire_all()
    return db.session.execute(db.select(JobRun).filter_by(task='sync').order_by(JobRun.id)).scalars().all()


def test_run_sync_keeps_its_result(app, redis, report):
    assert jobs.run_sync(source='oscar') == report

    last = jobs.last_sync()
    assert (last['source'], last['report']) == ('oscar', report) and last['finished_at'] >= last['started_at']
    assert not jobs.sync_running()


def test_sync_is_a_run_with_its_log(app, redis, report):
    jobs.run_sync(source='oscar')

    [run] = sync_runs()
    assert (run.state, run.device_id, run.username) == ('success', None, 'oscar')
    assert run.message == 'process 2 created, 1 disabled'
    assert 'info: Sync requested by oscar' in run.log and 'info: 120 devices with postprocessing' in run.log
    assert run.started_at and run.finished_at and jobs.last_sync()['run_id'] == run.id


def test_one_sync_at_a_time(app, redis, report):
    redis.set(jobs.SYNC_LOCK, 'other')

    assert jobs.run_sync() is None
    assert jobs.sync_running()
    assert [(run.state, run.message) for run in sync_runs()] == [('aborted', 'ALREADY_RUNNING')]


def test_redis_down_after_the_sync_keeps_its_result(app, redis, report, monkeypatch):
    def down(*args, **kwargs):
        raise ConnectionError('Redis down')

    monkeypatch.setattr(redis, 'set', lambda key, value, nx=False, ex=None: True if nx else down())
    monkeypatch.setattr(redis, 'eval', down)

    assert jobs.run_sync(source='oscar') == report
    [run] = sync_runs()
    assert run.state == 'success'


def test_failed_sync_is_recorded(app, redis, monkeypatch):
    def fail(details=None):
        raise RuntimeError('API down')
    monkeypatch.setattr(jobs, 'sync_jobs', fail)

    with pytest.raises(RuntimeError):
        jobs.run_sync()

    assert jobs.last_sync()['error'] == 'RuntimeError: API down'
    assert not jobs.sync_running()
    [run] = sync_runs()
    assert (run.state, run.message) == ('failed', 'RuntimeError: API down')
    assert 'error: Sync failed (RuntimeError: API down)' in run.log


def test_beat_syncs_every_hour():
    schedule = worker.app.conf.beat_schedule['sync-jobs']['schedule']
    assert schedule.minute == {0} and len(schedule.hour) == 24


def sign_in(client, role):
    user = next(user for user in USERS.values() if user['role'] == role)
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user)
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def test_sync_now_button(app, client, redis, report, queued, monkeypatch):
    monkeypatch.setattr(worker, '_flask_app', app)
    sign_in(client, 'admin')
    page = client.get('/jobs/').get_data(as_text=True)
    assert 'Sync now' in page and 'No sync recorded yet' in page

    page = client.post('/jobs/sync', data={'csrf': 'token'}, follow_redirects=True).get_data(as_text=True)
    [run] = sync_runs()
    assert queued == [run.id] and (run.state, run.username) == ('queued', 'admin') and 'Sync queued' in page
    # Queued: no second sync until it runs
    page = client.post('/jobs/sync', data={'csrf': 'token'}, follow_redirects=True).get_data(as_text=True)
    assert 'A sync is already running' in page and queued == [run.id]

    worker.sync_jobs(run_id=run.id)
    # The worker commits in its own session
    db.session.expire_all()
    page = client.get('/jobs/').get_data(as_text=True)
    assert '(admin)' in page and 'process 2 created, 1 disabled' in page
    assert f'/jobs/runs/{run.id}' in page
    page = client.get(f'/jobs/runs/{run.id}').get_data(as_text=True)
    assert 'Sync with the Smart Citizen API' in page and 'Sync requested by admin' in page and 'Device page' not in page

    redis.set(jobs.SYNC_LOCK, 'other')
    page = client.post('/jobs/sync', data={'csrf': 'token'}, follow_redirects=True).get_data(as_text=True)
    assert 'A sync is already running' in page and len(queued) == 1

    sign_in(client, 'researcher')
    assert client.post('/jobs/sync', data={'csrf': 'token'}).status_code == 403


def test_sync_api(app, client, sc_me, redis, report, queued):
    assert client.post('/api/v1/jobs/sync', headers=auth('researcher-token')).status_code == 403

    response = client.post('/api/v1/jobs/sync', headers=auth('admin-token'))
    assert response.status_code == 202 and queued == [response.get_json()['run']['id']]
    assert client.post('/api/v1/jobs/sync', headers=auth('admin-token')).status_code == 409
    db.session.execute(db.update(JobRun).values(state='success'))
    db.session.commit()

    redis.set(jobs.SYNC_LOCK, 'other')
    assert client.post('/api/v1/jobs/sync', headers=auth('admin-token')).status_code == 409
    assert client.get('/api/v1/jobs/sync', headers=auth('admin-token')).get_json() == {'running': True, 'last': None}


def test_sync_command(app, redis, report):
    runner = app.test_cli_runner()

    assert 'process: 2 created, 0 enabled, 1 disabled' in runner.invoke(args=['jobs', 'sync']).output
    assert jobs.last_sync()['source'] == 'command line'

    redis.set(jobs.SYNC_LOCK, 'other')
    result = runner.invoke(args=['jobs', 'sync'])
    assert result.exit_code == 1 and 'already running' in result.output


def test_sync_fails_when_the_lock_cannot_be_taken(app, redis, report, monkeypatch):
    def acquire(key, seconds=None):
        raise ConnectionError('redis unreachable')
    monkeypatch.setattr(jobs.locks, 'acquire', acquire)

    with pytest.raises(ConnectionError):
        jobs.run_sync()

    [run] = sync_runs()
    assert run.state == 'failed' and run.message.startswith('LOCK_FAILED')


def test_sync_that_cannot_be_queued_fails(app, client, redis, monkeypatch):
    def delay(run_id):
        raise ConnectionError('broker unreachable')
    monkeypatch.setattr(worker.sync_jobs, 'delay', delay)

    run = jobs.queue_sync('admin')

    assert run.state == 'failed' and run.message.startswith('NOT_QUEUED: ConnectionError')
    assert not jobs.sync_running()


def test_summary_leaves_out_zeros():
    assert jobs.sync_summary({'process': {'created': 0, 'enabled': 2, 'disabled': 0},
                              'backup': {'created': 0, 'enabled': 0, 'disabled': 0}}) == 'process 2 enabled'
    assert jobs.sync_summary({'process': {'created': 0, 'enabled': 0, 'disabled': 0}}) == 'no changes'
