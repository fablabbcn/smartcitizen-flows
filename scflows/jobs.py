''' Device jobs: which devices are processed or backed up, when, and their runs

- sync_jobs: creates and enables jobs from the Smart Citizen API (devices with valid postprocessing
  are processed, devices of researchers are backed up), and disables the ones that do not qualify
- dispatch_due_jobs: queues the runs of the jobs that are due
- execute_run: runs a task for a device, holding a lock so that it never runs twice at the same time
'''
import asyncio
import json
import random
from datetime import datetime, timedelta, timezone

import click
import pandas as pd
from flask.cli import AppGroup

from . import db, health, locks
from .config import config
from .custom_logger import logger
from .models import Job, JobRun

jobs_cli = AppGroup('jobs', help='Device jobs (processing and backups)')

INTERVALS = {
    Job.PROCESS: config._postprocessing_task_exec_interval_hours,
    Job.BACKUP: config._backup_task_exec_interval_hours,
    # Weekly unless the long blueprint says otherwise (meta.every_days)
    Job.LONG: 7 * 24,
}
# Results of the tasks: SUCCESS, FAILED or ABORTED (anything else)
STATES = {'SUCCESS': JobRun.SUCCESS, 'FAILED': JobRun.FAILED}


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return pd.Timestamp(value).tz_localize('UTC') if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value)


def aware(value):
    ''' SQLite returns naive datetimes '''
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


# Which devices qualify, from the Smart Citizen API and the blueprints of their hardware in flows

def hardware_kinds():
    ''' {hardware name: {blueprint kinds}} of the hardware in flows '''
    from .models import Hardware
    return {item.name: set(item.kinds) for item in db.session.execute(db.select(Hardware)).scalars()}


def kinds_of(device, kinds):
    ''' Blueprint kinds of a device's hardware (from its postprocessing), or None if it has no hardware in flows '''
    from .identity import hardware_name
    postprocessing = device.get('postprocessing') if isinstance(device, dict) else device.postprocessing
    if not postprocessing or not postprocessing.get('hardware_url'):
        return None
    return kinds.get(hardware_name(postprocessing['hardware_url']))


def devices_to_process(kinds=None):
    '''
    Devices whose hardware has a process blueprint, with valid postprocessing and readings newer than
    their last processing
    '''
    from smartcitizen_connector import search_by_query
    from smartcitizen_connector.device import check_postprocessing
    from .models import Blueprint

    kinds = hardware_kinds() if kinds is None else kinds
    devices = search_by_query(endpoint='devices',
                              search_items=[{'key': 'postprocessing_id', 'value': 'not_null', 'full': True}])
    result = set()
    for device_id in devices.index:
        device = devices.loc[device_id, :]
        postprocessing = device.postprocessing
        # Most postprocessing entries are empty
        if not postprocessing or not postprocessing.get('hardware_url') or pd.isna(device.last_reading_at):
            continue
        if Blueprint.PROCESS not in (kinds_of(device, kinds) or set()):
            continue
        latest = postprocessing.get('latest_postprocessing')
        if latest is not None and timestamp(latest) > timestamp(device.last_reading_at):
            continue
        _, _, valid = check_postprocessing(postprocessing)
        if valid:
            result.add(int(device_id))
    return result


def devices_for_long(kinds=None):
    ''' {device id: hours between runs} of devices with readings whose hardware has a long blueprint '''
    from smartcitizen_connector import search_by_query
    from .identity import hardware_name
    from .models import Blueprint, Hardware

    every = {}
    for item in db.session.execute(db.select(Hardware)).scalars():
        blueprint = item.blueprint_of(Blueprint.LONG)
        if blueprint is not None:
            every[item.name] = int((blueprint.meta.get('every_days') or 7) * 24)

    devices = search_by_query(endpoint='devices',
                              search_items=[{'key': 'postprocessing_id', 'value': 'not_null', 'full': True}])
    result = {}
    for device_id in devices.index:
        device = devices.loc[device_id, :]
        postprocessing = device.postprocessing
        if not postprocessing or not postprocessing.get('hardware_url') or pd.isna(device.last_reading_at):
            continue
        name = hardware_name(postprocessing['hardware_url'])
        if name in every:
            result[int(device_id)] = every[name]
    return result


def devices_to_back_up(kinds=None):
    '''
    Devices with readings whose hardware has a long blueprint (long processing reads the backups),
    and devices of researchers (role_mask 4) whose hardware has a backup blueprint or that have no
    hardware in flows
    '''
    from smartcitizen_connector import search_by_query
    from .models import Blueprint

    kinds = hardware_kinds() if kinds is None else kinds
    result = set()
    devices = search_by_query(endpoint='devices',
                              search_items=[{'key': 'postprocessing_id', 'value': 'not_null', 'full': True}])
    for device_id in devices.index:
        device = devices.loc[device_id, :]
        if not pd.isna(device.last_reading_at) and Blueprint.LONG in (kinds_of(device, kinds) or set()):
            result.add(int(device_id))

    users = search_by_query(endpoint='users',
                            search_items=[{'key': 'role_mask', 'search_matcher': 'eq', 'value': 4, 'full': True}])
    for user_id in users.index:
        for device in users.loc[user_id, 'devices']:
            if not device.get('last_reading_at'):
                continue
            device_kinds = kinds_of(device, kinds)
            if device_kinds is None or device_kinds & {Blueprint.BACKUP, Blueprint.LONG}:
                result.add(int(device['id']))
    return result


def sync_jobs(to_process=None, to_back_up=None, for_long=None):
    '''
    Creates or enables the jobs of the devices that qualify and disables the automatic ones that do not.
    Paused jobs stay paused. Returns {task: {'created': n, 'enabled': n, 'disabled': n}}
    '''
    kinds = hardware_kinds() if to_process is None or to_back_up is None else None
    # Long jobs run every few days, as their blueprint says: {device id: hours}
    # From the Smart Citizen API only on a full sync (no lists given)
    full = to_process is None and to_back_up is None and for_long is None
    long_every = (devices_for_long() if full else {}) if for_long is None else (
        dict(for_long) if isinstance(for_long, dict) else {device_id: INTERVALS[Job.LONG] for device_id in for_long})
    wanted = {Job.PROCESS: devices_to_process(kinds) if to_process is None else set(to_process),
              Job.BACKUP: devices_to_back_up(kinds) if to_back_up is None else set(to_back_up),
              Job.LONG: set(long_every)}
    report = {}
    for task, devices in wanted.items():
        counts = {'created': 0, 'enabled': 0, 'disabled': 0}
        jobs = {job.device_id: job for job in db.session.execute(db.select(Job).filter_by(task=task)).scalars()}
        for device_id in devices:
            job = jobs.get(device_id)
            if job is None:
                interval = long_every[device_id] if task == Job.LONG else INTERVALS[task]
                # Spread the first runs over the interval
                db.session.add(Job(device_id=device_id, task=task, interval_hours=interval, enabled=True,
                                   next_run_at=now() + timedelta(minutes=random.randint(0, interval * 60 - 1))))
                counts['created'] += 1
            elif not job.enabled:
                job.enabled = True
                counts['enabled'] += 1
        for device_id, job in jobs.items():
            if device_id not in devices and job.enabled and job.source == Job.AUTO:
                job.enabled = False
                counts['disabled'] += 1
        report[task] = counts
    db.session.commit()
    logger.info(f'Jobs synced: {report}')
    return report


# Runs

def next_run(job, at):
    ''' Next run after at, keeping the minute of the job so that runs stay spread '''
    following = aware(job.next_run_at) + timedelta(hours=job.interval_hours)
    if following <= at:
        intervals = (at - following) // timedelta(hours=job.interval_hours) + 1
        following += intervals * timedelta(hours=job.interval_hours)
    return following


def queue_run(device_id, task, job=None, dry_run=False, username=None):
    ''' Creates a run and sends it to the workers '''
    from .worker import run_job

    run = JobRun(job=job, device_id=device_id, task=task, dry_run=dry_run, username=username)
    db.session.add(run)
    db.session.commit()
    run_job.delay(run.id)
    return run


def find_job(device_id, task):
    return db.session.execute(db.select(Job).filter_by(device_id=device_id, task=task)).scalar_one_or_none()


def add_manual_job(device_id, task):
    ''' Adds a job requested by an admin (the sync does not disable it). Returns (job, created) '''
    job = find_job(device_id, task)
    if job is not None:
        return job, False
    interval = INTERVALS[task]
    job = Job(device_id=device_id, task=task, source=Job.MANUAL, enabled=True, interval_hours=interval,
              next_run_at=now() + timedelta(minutes=random.randint(0, interval * 60 - 1)))
    db.session.add(job)
    db.session.commit()
    return job, True


def set_paused(job, paused):
    job.paused = paused
    db.session.commit()
    logger.info(f'Job {job.task} {job.device_id} {"paused" if paused else "resumed"}')
    return job


def dispatch_due_jobs():
    ''' Queues the runs of active jobs that are due. Returns the queued runs '''
    at = now()
    jobs = db.session.execute(
        db.select(Job).where(Job.enabled.is_(True), Job.paused.is_(False), Job.next_run_at <= at)
        .order_by(Job.next_run_at).with_for_update(skip_locked=True)).scalars().all()
    runs = []
    for job in jobs:
        run = JobRun(job=job, device_id=job.device_id, task=job.task)
        db.session.add(run)
        job.next_run_at = next_run(job, at)
        job.last_queued_at = at
        runs.append(run)
    db.session.commit()

    from .worker import run_job
    for run in runs:
        run_job.delay(run.id)
    if runs:
        logger.info(f'Queued {len(runs)} runs')
    return runs


def task_function(task):
    if task == Job.PROCESS:
        from .tasks.dprocess import dprocess
        return lambda device_id, dry_run: dprocess(device_id, dry_run=dry_run)
    if task == Job.LONG:
        from .tasks.dlong import dlong
        return lambda device_id, dry_run: dlong(device_id, dry_run=dry_run)
    from .tasks.dbackup import dbackup
    return lambda device_id, dry_run: dbackup(device_id)


def execute_run(run_id):
    ''' Runs a queued run, unless the same task is already running for the device '''
    run = db.session.get(JobRun, run_id)
    if run is None or run.state != JobRun.QUEUED:
        return run

    key = f'scflows:lock:{run.task}:{run.device_id}'
    token = locks.acquire(key)
    if token is None:
        run.state, run.message, run.finished_at = JobRun.ABORTED, 'ALREADY_RUNNING', now()
        db.session.commit()
        return run

    try:
        run.state, run.started_at = JobRun.RUNNING, now()
        db.session.commit()
        # dprocess also returns the health checks of the data it processed
        log, state, *extra = asyncio.run(task_function(run.task)(run.device_id, run.dry_run))
        run.log = log
        run.state = STATES.get(state[0], JobRun.ABORTED)
        run.message = state[1]
        if extra and extra[0]:
            health.record(run.device_id, extra[0], run=run)
    except Exception as error:
        logger.exception(f'Run {run.id} failed')
        db.session.rollback()
        run = db.session.get(JobRun, run_id)
        run.state, run.message = JobRun.FAILED, f'{type(error).__name__}: {error}'
    finally:
        run.finished_at = now()
        db.session.commit()
        locks.release(key, token)
    return run


# Syncs: hourly from beat, or on request (/jobs/, the API, the command line)

SYNC_LOCK = 'scflows:lock:sync'
LAST_SYNC = 'scflows:last_sync'
# A sync takes a few minutes: the lock expires after this if a worker dies
SYNC_LOCK_SECONDS = 30 * 60


def run_sync(source='schedule'):
    '''
    Syncs the jobs with the Smart Citizen API, one sync at a time, and keeps its result for /jobs/.
    Returns the report, or None if a sync was already running
    '''
    token = locks.acquire(SYNC_LOCK, seconds=SYNC_LOCK_SECONDS)
    if token is None:
        logger.info('A sync is already running')
        return None
    started = now()
    result = {'source': source, 'started_at': started.isoformat()}
    try:
        result['report'] = sync_jobs()
        return result['report']
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['finished_at'] = now().isoformat()
        locks.client().set(LAST_SYNC, json.dumps(result))
        locks.release(SYNC_LOCK, token)


def last_sync():
    ''' Result of the last sync ({source, started_at, finished_at, report or error}), or None '''
    try:
        value = locks.client().get(LAST_SYNC)
    except Exception:
        return None
    return json.loads(value) if value else None


def sync_running():
    try:
        return bool(locks.client().exists(SYNC_LOCK))
    except Exception:
        return False


# Command line

@jobs_cli.command('sync')
def sync_command():
    ''' Create, enable and disable jobs from the Smart Citizen API '''
    report = run_sync(source='command line')
    if report is None:
        click.echo('A sync is already running')
        raise SystemExit(1)
    for task, counts in report.items():
        click.echo(f'{task}: {counts["created"]} created, {counts["enabled"]} enabled, {counts["disabled"]} disabled')


@jobs_cli.command('list')
@click.option('--task', type=click.Choice(Job.TASKS))
def list_command(task):
    ''' List jobs '''
    query = db.select(Job).order_by(Job.task, Job.device_id)
    if task:
        query = query.filter_by(task=task)
    for job in db.session.execute(query).scalars():
        status = 'paused' if job.paused else ('active' if job.enabled else 'disabled')
        click.echo(f'{job.task:8} {job.device_id:>6} {status:8} every {job.interval_hours}h, next {job.next_run_at:%Y-%m-%d %H:%M}')


@jobs_cli.command('run')
@click.argument('device_id', type=int)
@click.argument('task', type=click.Choice(Job.TASKS))
@click.option('--dry-run', is_flag=True, help='Process without posting (process only)')
@click.option('--inline', is_flag=True, help='Run here instead of sending it to the workers')
def run_command(device_id, task, dry_run, inline):
    ''' Run a task for a device now '''
    job = db.session.execute(db.select(Job).filter_by(device_id=device_id, task=task)).scalar_one_or_none()
    if inline:
        run = JobRun(job=job, device_id=device_id, task=task, dry_run=dry_run)
        db.session.add(run)
        db.session.commit()
        run = execute_run(run.id)
        click.echo(f'Run {run.id}: {run.state} {run.message}')
    else:
        run = queue_run(device_id, task, job=job, dry_run=dry_run)
        click.echo(f'Run {run.id} queued')
