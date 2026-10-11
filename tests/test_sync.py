import pytest
from flask import g

import scflows.jobs as jobs
import scflows.worker as worker

from conftest import USERS, auth


@pytest.fixture
def report(monkeypatch):
    result = {'process': {'created': 2, 'enabled': 0, 'disabled': 1}}
    monkeypatch.setattr(jobs, 'sync_jobs', lambda: result)
    return result


@pytest.fixture
def queued(monkeypatch):
    sent = []
    monkeypatch.setattr(worker.sync_jobs, 'delay', lambda source: sent.append(source))
    return sent


def test_run_sync_keeps_its_result(app, redis, report):
    assert jobs.run_sync(source='oscar') == report

    last = jobs.last_sync()
    assert (last['source'], last['report']) == ('oscar', report) and last['finished_at'] >= last['started_at']
    assert not jobs.sync_running()


def test_one_sync_at_a_time(app, redis, report):
    redis.set(jobs.SYNC_LOCK, 'other')

    assert jobs.run_sync() is None
    assert jobs.sync_running()


def test_failed_sync_is_recorded(app, redis, monkeypatch):
    def fail():
        raise RuntimeError('API down')
    monkeypatch.setattr(jobs, 'sync_jobs', fail)

    with pytest.raises(RuntimeError):
        jobs.run_sync()

    assert jobs.last_sync()['error'] == 'RuntimeError: API down'
    assert not jobs.sync_running()


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


def test_sync_now_button(app, client, redis, report, queued):
    sign_in(client, 'admin')
    page = client.get('/jobs/').get_data(as_text=True)
    assert 'Sync now' in page and 'No sync recorded yet' in page

    page = client.post('/jobs/sync', data={'csrf': 'token'}, follow_redirects=True).get_data(as_text=True)
    assert queued == ['admin'] and 'Sync queued' in page

    jobs.run_sync(source='admin')
    page = client.get('/jobs/').get_data(as_text=True)
    assert '(admin)' in page and 'process 2 created, 0 enabled, 1 disabled' in page

    redis.set(jobs.SYNC_LOCK, 'other')
    page = client.post('/jobs/sync', data={'csrf': 'token'}, follow_redirects=True).get_data(as_text=True)
    assert 'A sync is already running' in page and queued == ['admin']

    sign_in(client, 'researcher')
    assert client.post('/jobs/sync', data={'csrf': 'token'}).status_code == 403


def test_sync_api(app, client, sc_me, redis, report, queued):
    assert client.post('/api/v1/jobs/sync', headers=auth('researcher-token')).status_code == 403

    response = client.post('/api/v1/jobs/sync', headers=auth('admin-token'))
    assert response.status_code == 202 and queued == ['admin']

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
