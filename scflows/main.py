from datetime import timedelta

from flask import Blueprint, render_template, request
from flask_login import current_user
from werkzeug.exceptions import HTTPException

from . import db, health
from .access import visible_calibrations, visible_hardware
from .api import json_error
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


def health_figures():
    ''' Devices by status of their latest health checks '''
    items = health.latest(current_user.identity)
    return {status: sum(1 for item in items if item.status == status) for status in ('ok', 'warning', 'problem', 'error')}


@main.route('/')
def index():
    if not current_user.is_authenticated:
        return render_template('index.html')
    if current_user.is_admin:
        metadata = {'hardware': count(db.select(db.func.count(Hardware.id))),
                    'calibrations': count(db.select(db.func.count(Calibration.id))),
                    'blueprints': count(db.select(db.func.count(BlueprintModel.id)))}
        return render_template('index.html', metadata=metadata, jobs=job_figures(), health=health_figures())
    # Researchers: the metadata of their devices
    hardware = visible_hardware(current_user.identity)
    metadata = {'hardware': len(hardware), 'calibrations': len(visible_calibrations(current_user.identity)),
                'blueprints': len({item.blueprint_id for item in hardware})}
    return render_template('index.html', metadata=metadata, jobs=None, health=health_figures())


# Messages for the web interface. Others show the description of the error
MESSAGES = {
    403: 'Your account cannot open this page. Jobs are for Smart Citizen admins only.',
    404: 'This page does not exist, or the item was deleted.',
    500: 'Something went wrong on our side. Try again, and check the web logs if it keeps failing.',
}


@main.app_errorhandler(HTTPException)
def error_page(error):
    # The API answers in json, also for paths that do not exist
    if request.path.startswith('/api/'):
        return json_error(error)
    if error.response is not None:
        return error.response
    # Descriptions given with abort() explain the error better than the generic messages
    custom = error.description != type(error).description
    message = error.description if custom else MESSAGES.get(error.code, error.description)
    return render_template('error.html', error=error, message=message), error.code
