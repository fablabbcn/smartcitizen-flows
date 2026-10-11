from datetime import timedelta

import pytest
from flask import g

import scflows.jobs as jobs
from scflows import db, health
from scflows.models import DeviceHealth, JobRun

from conftest import USERS, auth


def summary(ratio, **values):
    return dict({'flagged': 1, 'checked': 10, 'ratio': ratio, 'intervals': [], 'more_intervals': 0}, **values)


def device_health(*columns, status='ok', name='GAPS'):
    return {'start': '2026-10-08T00:00:00+00:00', 'end': '2026-10-08T03:00:00+00:00', 'rows': 180,
            'device_name': 'Kit', 'blueprint': 'sc_air',
            'checks': [{'name': name, 'description': 'Find gaps', 'status': status,
                        'columns': {column: summary(ratio) for column, ratio in columns}}]}


def store(device_id, *columns, status='ok', days_ago=0):
    item = health.record(device_id, device_health(*columns, status=status))
    item.created_at = jobs.now() - timedelta(days=days_ago)
    db.session.commit()
    return item


# Rating

@pytest.mark.parametrize('ratios, expected', [
    ([0, 0], 'ok'), ([0, 0.05], 'warning'), ([0.05, 0.2], 'problem'), ([0.9], 'problem'), ([], 'ok'),
])
def test_device_status_is_the_worst_column(app, ratios, expected):
    item = store(1, *[(f'C{index}', ratio) for index, ratio in enumerate(ratios)])

    assert item.status == expected
    assert [column['status'] for column in item.checks[0]['columns'].values()] == \
        [health.column_status({'ratio': ratio}) for ratio in ratios]


def test_check_that_could_not_run_is_an_error(app):
    item = store(1, ('TEMP', 0.5), status='error: KeyError: limits')

    assert (item.status, item.checks[0]['rating']) == ('error', 'error')
    assert health.issues(item)[0][0] == ('GAPS', None, 'error', None)


def test_nothing_to_record(app):
    assert health.record(1, None) is None
    assert health.record(1, {'checks': []}) is None


def test_run_records_health(app, monkeypatch):
    monkeypatch.setattr(jobs.locks, 'acquire', lambda key, seconds=None: 'token')
    monkeypatch.setattr(jobs.locks, 'release', lambda key, token: None)

    def task_function(name):
        async def run(device_id, dry_run):
            return ['info: done'], ['SUCCESS', 'PROCESSED AND UPLOADED'], device_health(('TEMP', 0.5))
        return run

    monkeypatch.setattr(jobs, 'task_function', task_function)
    run = JobRun(device_id=7, task='process')
    db.session.add(run)
    db.session.commit()

    jobs.execute_run(run.id)

    item = db.session.execute(db.select(DeviceHealth)).scalar_one()
    assert (item.device_id, item.run_id, item.status, item.device_name) == (7, run.id, 'problem', 'Kit')


def test_prune_keeps_the_latest_of_each_device(app):
    old = store(1, ('TEMP', 0), days_ago=40).id
    store(1, ('TEMP', 0), days_ago=1)
    only = store(2, ('TEMP', 0), days_ago=40).id

    assert health.prune() == 1

    ids = set(db.session.execute(db.select(DeviceHealth.id)).scalars())
    assert old not in ids and only in ids


# API

def test_api_lists_latest_health(app, client, sc_me, monkeypatch):
    store(1, ('TEMP', 0))
    store(1, ('TEMP', 0.5), ('HUM', 0.1))
    store(2, ('TEMP', 0))

    assert client.get('/api/v1/devices/health').status_code == 401
    data = client.get('/api/v1/devices/health', headers=auth('admin-token')).get_json()

    assert [(item['device_id'], item['status']) for item in data] == [(1, 'problem'), (2, 'ok')]
    assert data[0]['issues'][0] == {'check': 'GAPS', 'column': 'TEMP', 'status': 'problem', 'ratio': 0.5}
    detail = client.get('/api/v1/devices/1/health', headers=auth('admin-token')).get_json()
    assert detail['latest']['checks'][0]['columns']['TEMP']['status'] == 'problem'
    assert [item['status'] for item in detail['history']] == ['problem', 'ok']
    assert client.get('/api/v1/devices/3/health', headers=auth('admin-token')).status_code == 404


def test_researchers_see_their_devices(app, client, sc_me, monkeypatch):
    store(1, ('TEMP', 0))
    store(2, ('TEMP', 0))
    monkeypatch.setitem(USERS, 'researcher-token', dict(USERS['researcher-token'], devices=[{'id': 2}]))

    data = client.get('/api/v1/devices/health', headers=auth('researcher-token')).get_json()

    assert [item['device_id'] for item in data] == [2]
    assert client.get('/api/v1/devices/1/health', headers=auth('researcher-token')).status_code == 403
    assert client.get('/api/v1/devices/health', headers=auth('citizen-token')).status_code == 403


# Interface

def sign_in(client, role, devices=()):
    user = dict(next(user for user in USERS.values() if user['role'] == role), devices=list(devices))
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = user
        session['_user_id'] = str(user['id'])


def test_health_pages(app, client):
    store(1, ('TEMP', 0))
    latest = store(1, ('NO2', 1.0))
    store(2, ('TEMP', 0.05))

    sign_in(client, 'admin')
    page = client.get('/health/').get_data(as_text=True)
    assert '/health/1' in page and '/health/2' in page
    # Worst first, with the worst issue
    assert page.index('/health/1') < page.index('/health/2') and '<span class="mono">NO2</span> 100.0%' in page
    assert '/health/2' not in client.get('/health/?status=problem').get_data(as_text=True)

    page = client.get('/health/1').get_data(as_text=True)
    assert 'NO2' in page and f'record={latest.id}' in page and 'selected' in page
    older = client.get(f'/health/1?record={latest.id - 1}').get_data(as_text=True)
    assert 'Older result' in older


def test_researcher_health_pages(app, client):
    store(1, ('TEMP', 0))
    store(2, ('TEMP', 0))

    sign_in(client, 'researcher', devices=[2, 5])
    page = client.get('/health/').get_data(as_text=True)

    assert '/health/2' in page and '/health/1' not in page
    # Their device 5 has no checks yet
    assert 'kits/5' in page and 'not checked' in page
    assert client.get('/health/1').status_code == 403
    assert client.get('/health/2').status_code == 200
    assert 'Health' in client.get('/').get_data(as_text=True)
