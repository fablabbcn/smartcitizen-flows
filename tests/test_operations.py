''' Admin operations on device jobs: run now, pause and resume, manual jobs '''
from datetime import timedelta

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


def test_run_options_must_be_an_object(client, sc_me, process_job, queued):
    response = client.post(f'/api/v1/jobs/{process_job.id}/run', json=[1], headers=auth('admin-token'))

    assert response.status_code == 400
    assert queued == []


def test_run_device_without_job(client, sc_me, queued):
    response = client.post('/api/v1/devices/99/backup/run', headers=auth('admin-token'))

    assert response.status_code == 202
    assert (response.get_json()['job_id'], response.get_json()['device_id']) == (None, 99)


def test_pause_and_resume(client, sc_me, process_job):
    response = client.patch(f'/api/v1/jobs/{process_job.id}', json={'paused': True}, headers=auth('admin-token'))
    assert response.get_json()['paused'] is True
    # Due now; the backup job of the device far away (new jobs start at a random time)
    db.session.expire_all()
    jobs.find_job(1, 'process').next_run_at = jobs.now() - timedelta(minutes=10)
    jobs.find_job(1, 'backup').next_run_at = jobs.now() + timedelta(hours=1)
    db.session.commit()
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

    client.post('/jobs/run', data={'csrf': 'token', 'device_id': '42', 'task': 'backup'})
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


# Several jobs at once

@pytest.fixture
def several(app):
    jobs.sync_jobs(to_process={1, 2, 3}, to_back_up={1, 2})
    return {(job.task, job.device_id): job.id for job in db.session.execute(db.select(Job)).scalars()}


def paused():
    db.session.expire_all()
    return {(job.task, job.device_id) for job in db.session.execute(db.select(Job).filter_by(paused=True)).scalars()}


def test_pause_and_resume_a_selection(client, several):
    sign_in(client, 'admin')
    selection = [several[('process', 1)], several[('process', 2)], several[('backup', 2)]]

    response = client.post('/jobs/selection', data={'csrf': 'token', 'action': 'pause', 'job_id': selection, 'task': 'process'})

    assert response.status_code == 302 and response.headers['Location'].endswith('/jobs/?task=process')
    assert paused() == {('process', 1), ('process', 2), ('backup', 2)}
    assert 'Paused 3 jobs' in client.get('/jobs/').get_data(as_text=True)

    client.post('/jobs/selection', data={'csrf': 'token', 'action': 'resume', 'job_id': selection[:1]})
    assert paused() == {('process', 2), ('backup', 2)}

    client.post('/jobs/selection', data={'csrf': 'token', 'action': 'pause', 'job_id': selection})
    assert 'Paused 1 jobs (2 already were)' in client.get('/jobs/').get_data(as_text=True)


def test_selection_needs_jobs_and_an_action(client, several):
    sign_in(client, 'admin')

    client.post('/jobs/selection', data={'csrf': 'token', 'action': 'pause'})
    client.post('/jobs/selection', data={'csrf': 'token', 'action': 'delete', 'job_id': [several[('process', 1)]]})

    assert paused() == set()
    assert 'Tick the jobs to pause or resume' in client.get('/jobs/').get_data(as_text=True)


def test_jobs_table_has_selection_boxes(client, several):
    sign_in(client, 'admin')
    text = client.get('/jobs/').get_data(as_text=True)

    assert 'data-select-all' in text and f'name="job_id" value="{several[("process", 1)]}" form="selection"' in text
    assert 'Pause selected' in text and 'Resume selected' in text


def test_api_pauses_several_jobs(client, sc_me, several):
    response = client.patch('/api/v1/jobs', json={'paused': True, 'device_ids': [1, 2], 'task': 'process'},
                            headers=auth('admin-token'))
    assert response.status_code == 200 and response.get_json()['changed'] == 2
    assert paused() == {('process', 1), ('process', 2)}

    # All the jobs of a device
    assert client.patch('/api/v1/jobs', json={'paused': True, 'device_ids': [2]},
                        headers=auth('admin-token')).get_json()['changed'] == 1
    response = client.patch('/api/v1/jobs', json={'paused': False, 'job_ids': [several[('process', 1)]]},
                            headers=auth('admin-token'))
    assert [job['device_id'] for job in response.get_json()['jobs']] == [1]
    assert paused() == {('process', 2), ('backup', 2)}


def test_api_selection_is_validated(client, sc_me, several):
    for body in ({'paused': True}, {'paused': 'yes', 'job_ids': [1]}, {'paused': True, 'job_ids': []},
                 {'paused': True, 'job_ids': [1], 'device_ids': [1]}, {'paused': True, 'device_ids': ['1']},
                 {'paused': True, 'device_ids': [1], 'task': 'other'}, {'paused': True, 'job_ids': [True]}):
        assert client.patch('/api/v1/jobs', json=body, headers=auth('admin-token')).status_code == 400, body
    assert client.patch('/api/v1/jobs', json={'paused': True, 'job_ids': [1]},
                        headers=auth('researcher-token')).status_code == 403
    assert paused() == set()


# From the device page

def test_device_page_runs_and_pauses_jobs(client, process_job, queued):
    sign_in(client, 'admin')
    page = client.get('/jobs/devices/1').get_data(as_text=True)
    assert f'action="/jobs/{process_job.id}/run"' in page and f'action="/jobs/{process_job.id}/pause"' in page
    assert 'name="dry_run" form="run-' in page and 'value="/jobs/devices/1"' in page

    response = client.post(f'/jobs/{process_job.id}/run', data={'csrf': 'token', 'dry_run': 'on', 'next': '/jobs/devices/1'})
    assert response.status_code == 302 and response.headers['Location'].endswith('/jobs/devices/1')
    assert run(queued[0]).dry_run

    response = client.post(f'/jobs/{process_job.id}/pause', data={'csrf': 'token', 'next': '/jobs/devices/1'})
    assert response.headers['Location'].endswith('/jobs/devices/1') and jobs.find_job(1, 'process').paused
    assert f'action="/jobs/{process_job.id}/resume"' in client.get('/jobs/devices/1').get_data(as_text=True)

    # Any task, without a job too
    response = client.post('/jobs/run', data={'csrf': 'token', 'device_id': '1', 'task': 'long', 'dry_run': 'on',
                                              'next': '/jobs/devices/1'})
    assert response.headers['Location'].endswith('/jobs/devices/1') and run(queued[-1]).task == 'long'


@pytest.mark.parametrize('target', ['https://example.com/', '//example.com/jobs/', '/metadata/', '/jobs/\\\\example.com',
                                    '/jobs/../', '/jobs/devices/../../metadata/', '/jobs/%2e%2e/', '/jobs/%252e%252e/',
                                    '/jobs/./../', '/jobs/%2F%2Fexample.com'])
def test_actions_only_go_back_to_jobs_pages(client, process_job, target):
    sign_in(client, 'admin')
    response = client.post(f'/jobs/{process_job.id}/pause', data={'csrf': 'token', 'next': target})
    assert response.headers['Location'].endswith('/jobs/')


def test_backups_have_no_dry_run(client, process_job, queued):
    sign_in(client, 'admin')
    page = client.post('/jobs/run', data={'csrf': 'token', 'device_id': '1', 'task': 'backup', 'dry_run': 'on',
                                          'next': '/jobs/devices/1'}, follow_redirects=True).get_data(as_text=True)
    assert queued == [] and 'Backups have no dry run' in page
    client.post('/jobs/run', data={'csrf': 'token', 'device_id': '1', 'task': 'backup'})
    assert len(queued) == 1 and not run(queued[0]).dry_run
