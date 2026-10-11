''' Web interface for device jobs, for admins '''
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import db
from .auth import admin_required
from .forms import protect
from .jobs import add_manual_job, find_job, queue_run, set_paused
from .models import Job, JobRun

jobs_ui = Blueprint('jobs_ui', __name__, url_prefix='/jobs')
protect(jobs_ui)


def latest_runs(job_ids):
    ''' Latest run of each job, in one query '''
    latest = db.select(db.func.max(JobRun.id)).where(JobRun.job_id.in_(job_ids)).group_by(JobRun.job_id)
    return {run.job_id: run for run in db.session.execute(db.select(JobRun).where(JobRun.id.in_(latest))).scalars()}


@jobs_ui.get('/')
@admin_required
def index():
    query = db.select(Job).order_by(Job.task, Job.device_id)
    if request.args.get('task') in Job.TASKS:
        query = query.filter_by(task=request.args['task'])
    jobs = db.session.execute(query).scalars().all()
    runs = db.session.execute(db.select(JobRun).order_by(JobRun.id.desc()).limit(50)).scalars().all()
    return render_template('jobs/index.html', jobs=jobs, latest=latest_runs([job.id for job in jobs]), runs=runs)


@jobs_ui.get('/runs/<int:run_id>')
@admin_required
def run(run_id):
    item = db.session.get(JobRun, run_id)
    if item is None:
        abort(404)
    return render_template('jobs/run.html', run=item)


def get_job(job_id):
    return db.session.get(Job, job_id) or abort(404)


def back():
    return redirect(url_for('jobs_ui.index'))


def form_device_and_task():
    device_id, task = request.form.get('device_id', '').strip(), request.form.get('task')
    if not device_id.isdigit() or task not in Job.TASKS:
        flash('Enter a device id and choose a task')
        return None, None
    return int(device_id), task


def queue(device_id, task, job=None):
    dry_run = task == Job.PROCESS and bool(request.form.get('dry_run'))
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
        flash(f'Added {task} job for device {device_id}' if created else
              f'Device {device_id} already has a {task} job')
    return back()
