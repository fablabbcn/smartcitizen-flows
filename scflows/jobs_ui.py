''' Web interface for device jobs, for admins '''
from flask import Blueprint, abort, render_template, request

from . import db
from .auth import admin_required
from .models import Job, JobRun

jobs_ui = Blueprint('jobs_ui', __name__, url_prefix='/jobs')


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
