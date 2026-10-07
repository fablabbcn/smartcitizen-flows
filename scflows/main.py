from datetime import timedelta

from flask import Blueprint, render_template
from flask_login import current_user

from . import db
from .identity import ADMIN
from .jobs import now
from .models import Blueprint as BlueprintModel, Calibration, Hardware, Job, JobRun

main = Blueprint('main', __name__)


def count(query):
    return db.session.execute(query).scalar_one()


def job_figures():
    jobs = db.select(db.func.count(Job.id)).filter_by(enabled=True)
    return {'active': count(jobs.filter_by(paused=False)),
            'paused': count(jobs.filter_by(paused=True)),
            'failed': count(db.select(db.func.count(JobRun.id)).where(
                JobRun.state == JobRun.FAILED, JobRun.created_at >= now() - timedelta(hours=24)))}


@main.route('/')
def index():
    if not current_user.is_authenticated:
        return render_template('index.html')
    metadata = {'hardware': count(db.select(db.func.count(Hardware.id))),
                'calibrations': count(db.select(db.func.count(Calibration.id))),
                'blueprints': count(db.select(db.func.count(BlueprintModel.id)))}
    return render_template('index.html', metadata=metadata,
                           jobs=job_figures() if current_user.role == ADMIN else None)
