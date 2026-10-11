''' Device jobs: which devices are processed or backed up, when, and their runs

- sync_jobs: creates and enables jobs from the Smart Citizen API (devices with valid postprocessing
  are processed, devices of researchers are backed up), and disables the ones that do not qualify
- dispatch_due_jobs: queues the runs of the jobs that are due
- execute_run: runs a task for a device, holding a lock so that it never runs twice at the same time
'''
import asyncio
import random
from datetime import datetime, timedelta, timezone

import click
import pandas as pd
from flask.cli import AppGroup

from . import db, locks
from .config import config
from .custom_logger import logger
from .models import Job, JobRun

jobs_cli = AppGroup('jobs', help='Device jobs (processing and backups)')

INTERVALS = {
    Job.PROCESS: config._postprocessing_task_exec_interval_hours,
    Job.BACKUP: config._backup_task_exec_interval_hours,
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


# Which devices qualify, from the Smart Citizen API

def devices_to_process():
    ''' Devices with valid postprocessing and readings newer than their last processing '''
    from smartcitizen_connector import search_by_query
    from smartcitizen_connector.device import check_postprocessing

    devices = search_by_query(endpoint='devices',
                              search_items=[{'key': 'postprocessing_id', 'value': 'not_null', 'full': True}])
    result = set()
    for device_id in devices.index:
        device = devices.loc[device_id, :]
        postprocessing = device.postprocessing
        # Most postprocessing entries are empty
        if not postprocessing or not postprocessing.get('hardware_url') or pd.isna(device.last_reading_at):
            continue
        latest = postprocessing.get('latest_postprocessing')
        if latest is not None and timestamp(latest) > timestamp(device.last_reading_at):
            continue
        _, _, valid = check_postprocessing(postprocessing)
        if valid:
            result.add(int(device_id))
    return result


def devices_to_back_up():
    ''' Devices with readings of researchers (role_mask 4) '''
    from smartcitizen_connector import search_by_query

    users = search_by_query(endpoint='users',
                            search_items=[{'key': 'role_mask', 'search_matcher': 'eq', 'value': 4, 'full': True}])
    return {int(device['id']) for user_id in users.index for device in users.loc[user_id, 'devices']
            if device.get('last_reading_at')}


def sync_jobs(to_process=None, to_back_up=None):
    '''
    Creates or enables the jobs of the devices that qualify and disables the automatic ones that do not.
    Paused jobs stay paused. Returns {task: {'created': n, 'enabled': n, 'disabled': n}}
    '''
    wanted = {Job.PROCESS: devices_to_process() if to_process is None else set(to_process),
              Job.BACKUP: devices_to_back_up() if to_back_up is None else set(to_back_up)}
    report = {}
    for task, devices in wanted.items():
        counts = {'created': 0, 'enabled': 0, 'disabled': 0}
        jobs = {job.device_id: job for job in db.session.execute(db.select(Job).filter_by(task=task)).scalars()}
        for device_id in devices:
            job = jobs.get(device_id)
            if job is None:
                interval = INTERVALS[task]
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
        log, state = asyncio.run(task_function(run.task)(run.device_id, run.dry_run))
        run.log = log
        run.state = STATES.get(state[0], JobRun.ABORTED)
        run.message = state[1]
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


# Command line

@jobs_cli.command('sync')
def sync_command():
    ''' Create, enable and disable jobs from the Smart Citizen API '''
    for task, counts in sync_jobs().items():
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
