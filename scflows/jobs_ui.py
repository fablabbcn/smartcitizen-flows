''' Web interface for device jobs, for admins '''
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import db, devices
from .auth import admin_required
from .forms import protect
from .jobs import (INTERVALS, add_manual_job, device_sync, find_job, last_sync, queue_run, queue_sync, set_paused, set_paused_many,
                   sync_running, sync_summary)
from .models import DeviceHealth, Job, JobRun
from .tasks.dprocess import posting_enabled

jobs_ui = Blueprint('jobs_ui', __name__, url_prefix='/jobs')
protect(jobs_ui)


@jobs_ui.app_template_filter('sync_summary')
def sync_summary_filter(report):
    return sync_summary(report)


@jobs_ui.app_template_filter('storage_link')
def storage_link(location):
    return devices.storage_link(location)


def device_names():
    '''
    {device id: name}: from the sync, or else from the latest health check of the device (manual jobs,
    devices the sync no longer lists)
    '''
    latest = db.select(db.func.max(DeviceHealth.id)).where(DeviceHealth.device_name.is_not(None)) \
        .group_by(DeviceHealth.device_id)
    names = dict(db.session.execute(db.select(DeviceHealth.device_id, DeviceHealth.device_name)
                                    .where(DeviceHealth.id.in_(latest))).all())
    names.update(db.session.execute(db.select(Job.device_id, Job.device_name)
                                    .where(Job.device_name.is_not(None))).all())
    return names


def latest_runs(job_ids):
    ''' Latest run of each job, in one query '''
    latest = db.select(db.func.max(JobRun.id)).where(JobRun.job_id.in_(job_ids)).group_by(JobRun.job_id)
    return {run.job_id: run for run in db.session.execute(db.select(JobRun).where(JobRun.id.in_(latest))).scalars()}


@jobs_ui.get('/')
@admin_required
def index():
    task = request.args.get('task') if request.args.get('task') in Job.TASKS else None
    query = db.select(Job).order_by(Job.task, Job.device_id)
    if task:
        query = query.filter_by(task=task)
    jobs = db.session.execute(query).scalars().all()
    counts = dict(db.session.execute(db.select(Job.task, db.func.count(Job.id)).group_by(Job.task)).all())
    runs = db.session.execute(db.select(JobRun).order_by(JobRun.id.desc()).limit(50)).scalars().all()
    hardware = devices.hardware_by_name(job.hardware for job in jobs)
    return render_template('jobs/index.html', jobs=jobs, latest=latest_runs([job.id for job in jobs]), runs=runs,
                           task=task, counts=counts, names=device_names(), last_sync=last_sync(),
                           sync_running=sync_running(), hardware=hardware, intervals=INTERVALS,
                           posting_enabled=posting_enabled(),
                           uncalibrated=devices.uncalibrated(hardware.values()))


@jobs_ui.get('/devices/<int:device_id>')
@admin_required
def device(device_id):
    ''' A device in flows: its jobs, hardware, calibrations, storage and latest runs '''
    jobs = db.session.execute(db.select(Job).filter_by(device_id=device_id).order_by(Job.task)).scalars().all()
    runs = db.session.execute(db.select(JobRun).filter_by(device_id=device_id).order_by(JobRun.id.desc())
                              .limit(30)).scalars().all()
    hardware_name = devices.device_hardware_name(device_id)
    hardware = devices.hardware_by_name([hardware_name]).get(hardware_name)
    calibrated = None
    if hardware is not None:
        missing = set(devices.uncalibrated([hardware])[hardware.name])
        calibrated = {sensor_id: sensor_id not in missing for sensor_id in hardware.sensor_ids}
    health = db.session.execute(db.select(DeviceHealth).filter_by(device_id=device_id)
                                .order_by(DeviceHealth.id.desc()).limit(1)).scalar()
    long_results = next((run.context.get('results') for run in runs if run.task == Job.LONG and run.context
                         and run.context.get('results')), None)
    decisions, sync_run = device_sync(device_id)
    return render_template('jobs/device.html', device_id=device_id, name=devices.device_name(device_id), jobs=jobs,
                           decisions=decisions, sync_run=sync_run,
                           runs=runs, latest=latest_runs([job.id for job in jobs]), hardware_name=hardware_name,
                           hardware=hardware, calibrated=calibrated, health=health,
                           backups=devices.backup_location(device_id), long_results=long_results)


@jobs_ui.post('/sync')
@admin_required
def sync_now():
    ''' Queues a sync with the Smart Citizen API (it takes a few minutes) '''
    if sync_running():
        flash('A sync is already running. Reload the page in a few minutes.', 'warning')
    else:
        run = queue_sync(current_user.username)
        flash(f'Sync queued (run {run.id}). New and changed jobs will appear in a few minutes.', 'info')
    return back()


@jobs_ui.get('/runs/<int:run_id>')
@admin_required
def run(run_id):
    item = db.session.get(JobRun, run_id)
    if item is None:
        abort(404)
    context = item.context or {}
    # Sensor ids of the hardware as the run saw them: {sensor id: calibrated}
    missing = set(context.get('uncalibrated') or [])
    calibrated = {sensor_id: sensor_id not in missing for version in context.get('versions') or []
                  for sensor_id in version.get('ids', {}).values()}
    known = devices.hardware_by_name([context.get('hardware')]) if context.get('hardware') else {}
    return render_template('jobs/run.html', run=item, context=context, calibrated=calibrated,
                           names=device_names() if item.task == JobRun.SYNC else {},
                           hardware_in_flows=context.get('hardware') in known,
                           name=context.get('device_name') or (devices.device_name(item.device_id) if item.device_id else None))


def get_job(job_id):
    return db.session.get(Job, job_id) or abort(404)


def back():
    ''' To the page the action came from (a page of the jobs interface), or the jobs page '''
    target = request.form.get('next', '')
    if target.startswith('/jobs/') and '//' not in target and '\\' not in target:
        return redirect(target)
    return redirect(url_for('jobs_ui.index'))


def form_device_and_task():
    device_id, task = request.form.get('device_id', '').strip(), request.form.get('task')
    if not device_id.isdigit() or task not in Job.TASKS:
        flash('Enter a device id and choose a task', 'error')
        return None, None
    return int(device_id), task


def queue(device_id, task, job=None):
    # Dry runs process without posting (process) or storing (long)
    dry_run = task in (Job.PROCESS, Job.LONG) and bool(request.form.get('dry_run'))
    run = queue_run(device_id, task, job=job, dry_run=dry_run, username=current_user.username)
    flash(f'Run {run.id} queued: {task} device {device_id}' + (' (dry run)' if dry_run else ''))


@jobs_ui.post('/<int:job_id>/run')
@admin_required
def run_job(job_id):
    job = get_job(job_id)
    queue(job.device_id, job.task, job=job)
    return back()


@jobs_ui.post('/<int:job_id>/pause')
@admin_required
def pause_job(job_id):
    job = set_paused(get_job(job_id), True)
    flash(f'Paused {job.task} for device {job.device_id}')
    return back()


@jobs_ui.post('/<int:job_id>/resume')
@admin_required
def resume_job(job_id):
    job = set_paused(get_job(job_id), False)
    flash(f'Resumed {job.task} for device {job.device_id}')
    return back()


@jobs_ui.post('/selection')
@admin_required
def pause_selection():
    ''' Pauses or resumes the jobs ticked in the table '''
    action = request.form.get('action')
    ids = [int(value) for value in request.form.getlist('job_id') if value.isdigit()]
    task = request.form.get('task') if request.form.get('task') in Job.TASKS else None
    if action not in ('pause', 'resume') or not ids:
        flash('Select at least one job to pause or resume', 'error')
    else:
        selected = db.session.execute(db.select(Job).where(Job.id.in_(ids))).scalars().all()
        changed = set_paused_many(selected, action == 'pause')
        done = 'Paused' if action == 'pause' else 'Resumed'
        flash(f'{done} {len(changed)} jobs' + (f' ({len(selected) - len(changed)} already {done.lower()})' if len(changed) < len(selected) else ''))
    return redirect(url_for('jobs_ui.index', task=task))


@jobs_ui.post('/run')
@admin_required
def run_device():
    device_id, task = form_device_and_task()
    if device_id is not None:
        queue(device_id, task, job=find_job(device_id, task))
    return back()


@jobs_ui.post('/add')
@admin_required
def add_job():
    device_id, task = form_device_and_task()
    if device_id is not None:
        job, created = add_manual_job(device_id, task)
        if created:
            flash(f'Added {task} job for device {device_id}')
        else:
            flash(f'Device {device_id} already has a {task} job', 'warning')
    return back()
