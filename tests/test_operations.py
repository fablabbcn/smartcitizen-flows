''' Admin operations on device jobs: run now, pause and resume, manual jobs '''
import pytest
from flask import g

import scflows.jobs as jobs
import scflows.worker as worker
from scflows import db
from scflows.models import Job, JobRun

from conftest import USERS, auth


@pytest.fixture
def queued(monkeypatch):
    sent = []
    monkeypatch.setattr(worker.run_job, 'delay', lambda run_id: sent.append(run_id))
    return sent


@pytest.fixture
def process_job(app):
    jobs.sync_jobs(to_process={1}, to_back_up={1})
    return jobs.find_job(1, 'process')


def run(run_id):
    db.session.expire_all()
    return db.session.get(JobRun, run_id)


# API

def test_operations_require_admin(client, sc_me, process_job, queued):
    for method, path in [('post', f'/api/v1/jobs/{process_job.id}/run'), ('patch', f'/api/v1/jobs/{process_job.id}'),
                         ('post', '/api/v1/devices/1/backup/run'), ('post', '/api/v1/jobs')]:
        assert getattr(client, method)(path, json={}, headers=auth('researcher-token')).status_code == 403
    assert queued == []


def test_run_job_now(client, sc_me, process_job, queued):
    response = client.post(f'/api/v1/jobs/{process_job.id}/run', json={'dry_run': True}, headers=auth('admin-token'))

    assert response.status_code == 202
    data = response.get_json()
    assert (data['state'], data['dry_run'], data['username'], data['job_id']) == ('queued', True, 'admin', process_job.id)
    assert queued == [data['id']]


def test_backup_has_no_dry_run(client, sc_me, process_job, queued):
    response = client.post('/api/v1/devices/1/backup/run', json={'dry_run': True}, headers=auth('admin-token'))

    assert response.status_code == 400
    assert queued == []


def test_run_device_without_job(client, sc_me, queued):
    response = client.post('/api/v1/devices/99/backup/run', headers=auth('admin-token'))

    assert response.status_code == 202
    assert (response.get_json()['job_id'], response.get_json()['device_id']) == (None, 99)


def test_pause_and_resume(client, sc_me, process_job):
    response = client.patch(f'/api/v1/jobs/{process_job.id}', json={'paused': True}, headers=auth('admin-token'))
    assert response.get_json()['paused'] is True
    assert jobs.dispatch_due_jobs() == []

    assert client.patch(f'/api/v1/jobs/{process_job.id}', json={'paused': 'no'},
                        headers=auth('admin-token')).status_code == 400
    response = client.patch(f'/api/v1/jobs/{process_job.id}', json={'paused': False}, headers=auth('admin-token'))
    assert response.get_json()['paused'] is False


def test_add_manual_job(client, sc_me, process_job):
    response = client.post('/api/v1/jobs', json={'device_id': 7, 'task': 'process'}, headers=auth('admin-token'))

    assert response.status_code == 201
    assert response.get_json()['source'] == 'manual'
    assert client.post('/api/v1/jobs', json={'device_id': 7, 'task': 'process'},
                       headers=auth('admin-token')).status_code == 409
    assert client.post('/api/v1/jobs', json={'device_id': '7', 'task': 'other'},
                       headers=auth('admin-token')).status_code == 400
    # The sync does not disable manual jobs
    jobs.sync_jobs(to_process={1}, to_back_up={1})
    assert jobs.find_job(7, 'process').enabled


# Interface

def sign_in(client, role):
    user = next(user for user in USERS.values() if user['role'] == role)
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user)
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def test_interface_run_now(client, process_job, queued):
    sign_in(client, 'admin')

    response = client.post(f'/jobs/{process_job.id}/run', data={'csrf': 'token', 'dry_run': 'on'})

    assert response.status_code == 302
    item = run(queued[0])
    assert (item.device_id, item.task, item.dry_run, item.username) == (1, 'process', True, 'admin')
    assert f'Run {item.id} queued: process device 1 (dry run)' in client.get('/jobs/').get_data(as_text=True)


def test_interface_pause_resume(client, process_job):
    sign_in(client, 'admin')

    client.post(f'/jobs/{process_job.id}/pause', data={'csrf': 'token'})
    assert jobs.find_job(1, 'process').paused
    assert 'Resume' in client.get('/jobs/').get_data(as_text=True)

    client.post(f'/jobs/{process_job.id}/resume', data={'csrf': 'token'})
    db.session.expire_all()
    assert not jobs.find_job(1, 'process').paused


def test_interface_run_and_add_for_a_device(client, process_job, queued):
    sign_in(client, 'admin')

    client.post('/jobs/run', data={'csrf': 'token', 'device_id': '42', 'task': 'backup', 'dry_run': 'on'})
    item = run(queued[0])
    assert (item.device_id, item.task, item.dry_run) == (42, 'backup', False)

    client.post('/jobs/add', data={'csrf': 'token', 'device_id': '42', 'task': 'backup'})
    assert jobs.find_job(42, 'backup').source == 'manual'
    page = client.post('/jobs/add', data={'csrf': 'token', 'device_id': 'x', 'task': 'backup'},
                       follow_redirects=True).get_data(as_text=True)
    assert 'Enter a device id' in page


def test_interface_requires_admin_and_csrf(client, process_job, queued):
    sign_in(client, 'researcher')
    assert client.post(f'/jobs/{process_job.id}/run', data={'csrf': 'token'}).status_code == 403

    sign_in(client, 'admin')
    assert client.post(f'/jobs/{process_job.id}/run', data={'csrf': 'other'}).status_code == 400
    assert queued == []
