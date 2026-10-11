''' Public read API for processing metadata

Paths follow the layout of the smartcitizen-data repository, so that
{base}/hardware/<name>.json, {base}/blueprints/<name>.json and
{base}/calibrations/calibrations.json work with base = <host>/api/v1/
'''
import re

from importlib.metadata import PackageNotFoundError, version

from flask import Blueprint, abort, current_app, jsonify, request, url_for
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from . import db, editing
from . import health as device_health
from .identity import ADMIN, EDITORS, current_identity, requires_role
from .models import Blueprint as BlueprintModel, Calibration, Hardware, Job, JobRun, ParameterSet, SensorName
from .validation import check_hardware

api = Blueprint('api', __name__, url_prefix='/api/v1')

CACHE_CONTROL = 'public, max-age=300'


def external_url(endpoint, **values):
    ''' Absolute url, using PUBLIC_URL when set '''
    if current_app.config['PUBLIC_URL']:
        return current_app.config['PUBLIC_URL'] + url_for(endpoint, **values)
    return url_for(endpoint, _external=True, **values)


def blueprint_url(name):
    return external_url('api.get_blueprint', name=f'{name}.json')


def hardware_json(item):
    ''' Hardware, with the url of its blueprint in flows '''
    return item.to_json(blueprint_url=blueprint_url(item.blueprint.name) if item.blueprint else None)


def get_by_name(model, name, field='name'):
    item = db.session.execute(
        db.select(model).filter_by(**{field: name.removesuffix('.json')})).scalar_one_or_none()
    if item is None:
        abort(404)
    return item


@api.after_request
def add_cache_control(response):
    if request.method in ['GET', 'HEAD'] and response.status_code == 200:
        response.headers['Cache-Control'] = CACHE_CONTROL
    return response


@api.errorhandler(HTTPException)
def json_error(error):
    # Errors raised with a ready response (see unprocessable) keep it
    if error.response is not None:
        return error.response
    return jsonify({'error': error.name, 'message': error.description}), error.code


@api.get('/')
def index():
    try:
        package_version = version('scflows')
    except PackageNotFoundError:
        package_version = None
    return {
        'name': 'Smart Citizen Flows',
        'version': package_version,
        'documentation': 'https://github.com/fablabbcn/smartcitizen-flows#processing-metadata',
        'links': {
            'blueprints': external_url('api.list_blueprints'),
            'hardware': external_url('api.list_hardware'),
            'calibrations': external_url('api.list_calibrations'),
            'names': external_url('api.list_names'),
            'parameters': external_url('api.list_parameters'),
            'device_health': external_url('api.list_device_health'),
            'health': external_url('api.health'),
        },
    }


@api.get('/health')
def health():
    try:
        db.session.execute(db.text('SELECT 1'))
    except Exception:
        # Details go to the log, not to the response
        current_app.logger.exception('Health check failed')
        db.session.rollback()
        return {'status': 'unhealthy'}, 503
    return {'status': 'ok'}


@api.get('/blueprints')
def list_blueprints():
    blueprints = db.session.execute(db.select(BlueprintModel).order_by(BlueprintModel.name)).scalars()
    return jsonify([{'name': blueprint.name, 'kind': blueprint.kind, 'url': blueprint_url(blueprint.name)}
                    for blueprint in blueprints])


@api.get('/blueprints/<name>')
def get_blueprint(name):
    return jsonify(get_by_name(BlueprintModel, name).to_json())


@api.get('/hardware')
def list_hardware():
    hardware = db.session.execute(db.select(Hardware).order_by(Hardware.name)).scalars()
    return jsonify([{'name': item.name,
                     'description': item.description,
                     'blueprint': item.blueprint.name if item.blueprint else None,
                     'blueprints': [blueprint.name for blueprint in item.blueprints],
                     'url': external_url('api.get_hardware', name=f'{item.name}.json')}
                    for item in hardware])


@api.get('/hardware/<name>')
def get_hardware(name):
    return jsonify(hardware_json(get_by_name(Hardware, name)))


@api.get('/calibrations/calibrations.json', endpoint='calibrations_file')
@api.get('/calibrations')
def list_calibrations():
    ''' All calibrations as {sensor_id: data}, optionally filtered with ?kind= '''
    query = db.select(Calibration).order_by(Calibration.sensor_id)
    if 'kind' in request.args:
        query = query.filter_by(kind=request.args['kind'])
    return jsonify({item.sensor_id: item.to_json() for item in db.session.execute(query).scalars()})


@api.get('/calibrations/<sensor_id>')
def get_calibration(sensor_id):
    return jsonify(get_by_name(Calibration, sensor_id, field='sensor_id').to_json())


# Long processing parameters per sensor type (see parameters.py)

@api.get('/parameters')
def list_parameters():
    ''' {sensor type: parameters} of the types with parameters, and the types they can be set for '''
    from .parameters import sensor_types
    items = db.session.execute(db.select(ParameterSet).order_by(ParameterSet.name)).scalars()
    return jsonify({'parameters': {item.name: item.to_json() for item in items}, 'sensor_types': sensor_types()})


@api.get('/parameters/<name>')
def get_parameters(name):
    return jsonify(get_by_name(ParameterSet, name).to_json())


@api.put('/parameters/<name>')
@requires_role(ADMIN)
def put_parameters(name):
    try:
        item, created = editing.save_parameter_set(key_of(name), json_body(), identity=current_identity())
    except ValidationError as error:
        unprocessable(validation_errors(error))
    except editing.UnknownSensorType as error:
        unprocessable([str(error)])
    db.session.commit()
    return saved(item.to_json(), created)


@api.delete('/parameters/<name>')
@requires_role(ADMIN)
def delete_parameters(name):
    return delete_item('parameters', name)


# SCDevice is the handler scdata looks the names up with
@api.get('/names/SCDevice.json', endpoint='names_file')
@api.get('/names')
def list_names():
    ''' Sensor names in order, as [{name, id, description, unit}] '''
    names = db.session.execute(db.select(SensorName).order_by(SensorName.position)).scalars()
    return jsonify([item.to_json() for item in names])


@api.get('/names/<name>')
def get_name(name):
    return jsonify(get_by_name(SensorName, name).to_json())


# Writes and deletes: admins. Reads are public (processing reads the metadata without a token)

KEY_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def key_of(value):
    key = value.removesuffix('.json')
    if not KEY_PATTERN.match(key):
        abort(400, 'Names can only contain letters, numbers, "_" and "-" (64 characters at most)')
    return key


def json_body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, 'The body must be a json object')
    return body


def unprocessable(errors):
    response = jsonify({'error': 'Unprocessable Entity', 'message': 'Invalid content', 'errors': errors})
    response.status_code = 422
    abort(response)


def validation_errors(error):
    return [f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}" for item in error.errors()]


def saved(data, created, warnings=None):
    response = jsonify({'data': data, 'warnings': warnings or []})
    response.status_code = 201 if created else 200
    return response


@api.put('/blueprints/<name>')
@requires_role(ADMIN)
def put_blueprint(name):
    try:
        item, created = editing.save_blueprint(key_of(name), json_body(), identity=current_identity())
    except ValidationError as error:
        unprocessable(validation_errors(error))
    db.session.commit()
    return saved(item.to_json(), created)


@api.put('/hardware/<name>')
@requires_role(ADMIN)
def put_hardware(name):
    key = key_of(name)
    check = check_hardware(json_body())
    if not check.valid:
        unprocessable(check.errors)
    try:
        item, created = editing.save_hardware(key, check.hardware, identity=current_identity())
    except editing.BlueprintNotInFlows as error:
        # The blueprint was deleted after the check
        unprocessable([f'blueprint: {error}'])
    db.session.commit()
    return saved(hardware_json(item), created, check.warnings)


@api.post('/hardware/<name>/check')
def post_hardware_check(name):
    ''' Checks a hardware description without saving it '''
    key_of(name)
    return jsonify(check_hardware(json_body()).to_json())


@api.put('/calibrations/<sensor_id>')
@requires_role(ADMIN)
def put_calibration(sensor_id):
    try:
        item, created = editing.save_calibration(key_of(sensor_id), json_body(), identity=current_identity())
    except ValidationError as error:
        unprocessable(validation_errors(error))
    db.session.commit()
    return saved(item.to_json(), created)


@api.put('/names/<name>')
@requires_role(ADMIN)
def put_name(name):
    try:
        item, created = editing.save_name(key_of(name), json_body(), identity=current_identity())
    except ValidationError as error:
        unprocessable(validation_errors(error))
    except editing.NameMismatch as error:
        unprocessable([str(error)])
    db.session.commit()
    return saved(item.to_json(), created)


def delete_item(kind, key):
    try:
        deleted = editing.delete(kind, key_of(key), identity=current_identity())
    except editing.BlueprintInUse as error:
        abort(409, f'The blueprint is used by hardware: {", ".join(error.hardware)}')
    if not deleted:
        abort(404)
    db.session.commit()
    return '', 204


@api.delete('/blueprints/<name>')
@requires_role(ADMIN)
def delete_blueprint(name):
    return delete_item('blueprint', name)


@api.delete('/hardware/<name>')
@requires_role(ADMIN)
def delete_hardware(name):
    return delete_item('hardware', name)


@api.delete('/calibrations/<sensor_id>')
@requires_role(ADMIN)
def delete_calibration(sensor_id):
    return delete_item('calibration', sensor_id)


@api.delete('/names/<name>')
@requires_role(ADMIN)
def delete_name(name):
    return delete_item('name', name)


@api.get('/<any(blueprints, hardware, calibrations, names, parameters):kinds>/<key>/revisions')
def get_revisions(kinds, key):
    kind = {'blueprints': 'blueprint', 'hardware': 'hardware', 'calibrations': 'calibration', 'names': 'name',
            'parameters': 'parameters'}[kinds]
    return jsonify([revision.to_json() for revision in editing.revisions(kind, key_of(key))])


# Device health: admins see every device, researchers their own

@api.get('/devices/health')
@requires_role(*EDITORS)
def list_device_health():
    ''' Latest health of each device, with its worst issues '''
    result = []
    for item in device_health.latest(current_identity()):
        found, more = device_health.issues(item)
        result.append(dict(item.to_json(checks=False),
                           url=external_url('api.get_device_health', device_id=item.device_id),
                           issues=[{'check': check, 'column': column, 'status': status, 'ratio': ratio}
                                   for check, column, status, ratio in found], more_issues=more))
    return jsonify(result)


@api.get('/devices/<int:device_id>/health')
@requires_role(*EDITORS)
def get_device_health(device_id):
    ''' Latest health of a device in full, and its history (?limit=, 50 by default) '''
    if not device_health.can_see_device(current_identity(), device_id):
        abort(403, 'Researchers can only see the health of their devices')
    limit = min(request.args.get('limit', 50, type=int), 500)
    items = device_health.history(device_id, limit=limit)
    if not items:
        abort(404, 'No health checks for this device yet')
    return jsonify({'latest': items[0].to_json(),
                    'history': [item.to_json(checks=False) for item in items]})


SERIES_MAX_POINTS = 20000


@api.get('/devices/<int:device_id>/series')
@requires_role(*EDITORS)
def get_device_series(device_id):
    '''
    Results of long processing (baselines over months of backups): ?blueprint= (the long blueprint
    of the device's hardware by default), ?channels=CO2,NO2, ?from= and ?to= (dates), ?resample=
    (1h by default, raw for none) and ?format=csv
    '''
    from pandas import Timedelta
    from . import storage

    if not device_health.can_see_device(current_identity(), device_id):
        abort(403, 'Researchers can only see the data of their devices')
    blueprint = request.args.get('blueprint') or long_blueprint_of(device_id)
    if blueprint is None:
        abort(404, 'No long processing for this device: add a long blueprint to its hardware')
    channels = [name for name in request.args.get('channels', '').split(',') if name] or None
    data = storage.read_processed(device_id, key_of(blueprint), channels=channels,
                                  start=request.args.get('from'), end=request.args.get('to'))
    if data is None:
        abort(404, f'No results of {blueprint} for this device yet')
    resample = request.args.get('resample', '1h')
    if resample != 'raw':
        try:
            Timedelta(resample)
        except ValueError:
            abort(400, 'resample: a pandas frequency (e.g. 10min, 1h, 1D) or raw')
        data = data.resample(resample).mean()
    if len(data) > SERIES_MAX_POINTS:
        abort(400, f'{len(data)} points: ask for a shorter period or a longer resample (at most {SERIES_MAX_POINTS})')
    if request.args.get('format') == 'csv':
        return current_app.response_class(data.to_csv(index_label='TIME'), mimetype='text/csv',
                                          headers={'Content-Disposition': f'attachment; filename={device_id}-{blueprint}.csv'})
    return jsonify({'device_id': device_id, 'blueprint': blueprint, 'resample': resample,
                    'run': storage.read_run_info(device_id, blueprint),
                    'index': [timestamp.isoformat() for timestamp in data.index],
                    'channels': {name: [None if value != value else round(float(value), 4) for value in data[name]]
                                 for name in data.columns}})


def long_blueprint_of(device_id):
    ''' Name of the long blueprint of a device, from its latest long run '''
    items = device_health.history(device_id, limit=1, task=Job.LONG)
    return items[0].blueprint if items else None


# Jobs and runs: admins

@api.get('/jobs/sync')
@requires_role(ADMIN)
def get_sync():
    ''' Whether a sync is running, and the result of the last one '''
    from .jobs import last_sync, sync_running
    return jsonify({'running': sync_running(), 'last': last_sync()})


@api.post('/jobs/sync')
@requires_role(ADMIN)
def post_sync():
    ''' Queues a sync with the Smart Citizen API (it takes a few minutes): 202, or 409 if one is running '''
    from . import worker
    from .jobs import sync_running
    if sync_running():
        abort(409, 'A sync is already running')
    worker.sync_jobs.delay(source=current_identity().username)
    return jsonify({'queued': True}), 202


@api.get('/jobs')
@requires_role(ADMIN)
def list_jobs():
    query = db.select(Job).order_by(Job.task, Job.device_id)
    if request.args.get('task'):
        query = query.filter_by(task=request.args['task'])
    if request.args.get('device_id', '').isdigit():
        query = query.filter_by(device_id=int(request.args['device_id']))
    return jsonify([job.to_json() for job in db.session.execute(query).scalars()])


@api.get('/jobs/<int:job_id>')
@requires_role(ADMIN)
def get_job(job_id):
    job = db.session.get(Job, job_id) or abort(404)
    return jsonify(job.to_json())


@api.get('/jobs/<int:job_id>/runs')
@requires_role(ADMIN)
def get_job_runs(job_id):
    job = db.session.get(Job, job_id) or abort(404)
    return jsonify([run.to_json() for run in job.runs.limit(100)])


@api.get('/runs/<int:run_id>')
@requires_role(ADMIN)
def get_run(run_id):
    run = db.session.get(JobRun, run_id) or abort(404)
    return jsonify(run.to_json(log=True))


def run_options(task):
    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get('dry_run', False))
    if dry_run and task not in (Job.PROCESS, Job.LONG):
        abort(400, 'dry_run is only available for process and long')
    return dry_run


@api.post('/jobs')
@requires_role(ADMIN)
def post_job():
    ''' Adds a manual job: {"device_id": 123, "task": "process"} '''
    from .jobs import add_manual_job
    body = json_body()
    if not isinstance(body.get('device_id'), int) or body.get('task') not in Job.TASKS:
        abort(400, f'Send {{"device_id": <int>, "task": <{" or ".join(Job.TASKS)}>}}')
    job, created = add_manual_job(body['device_id'], body['task'])
    if not created:
        abort(409, f'Job {job.id} already exists for this device and task')
    return jsonify(job.to_json()), 201


@api.patch('/jobs/<int:job_id>')
@requires_role(ADMIN)
def patch_job(job_id):
    ''' Pauses or resumes a job: {"paused": true} '''
    from .jobs import set_paused
    job = db.session.get(Job, job_id) or abort(404)
    body = json_body()
    if not isinstance(body.get('paused'), bool):
        abort(400, 'Send {"paused": true} or {"paused": false}')
    return jsonify(set_paused(job, body['paused']).to_json())


@api.post('/jobs/<int:job_id>/run')
@requires_role(ADMIN)
def post_job_run(job_id):
    ''' Runs a job now. Optional body: {"dry_run": true} (process) '''
    from .jobs import queue_run
    job = db.session.get(Job, job_id) or abort(404)
    run = queue_run(job.device_id, job.task, job=job, dry_run=run_options(job.task),
                    username=current_identity().username)
    return jsonify(run.to_json()), 202


@api.post('/devices/<int:device_id>/<any(process, backup):task>/run')
@requires_role(ADMIN)
def post_device_run(device_id, task):
    ''' Runs a task for a device now, with or without a job. Optional body: {"dry_run": true} (process) '''
    from .jobs import find_job, queue_run
    run = queue_run(device_id, task, job=find_job(device_id, task), dry_run=run_options(task),
                    username=current_identity().username)
    return jsonify(run.to_json()), 202
