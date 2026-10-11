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
from .identity import ADMIN, EDITORS, current_identity, requires_role
from .models import Blueprint as BlueprintModel, Calibration, Hardware
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
    return item.to_json(blueprint_url=blueprint_url(item.blueprint.name))


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
    return jsonify([{'name': blueprint.name, 'url': blueprint_url(blueprint.name)} for blueprint in blueprints])


@api.get('/blueprints/<name>')
def get_blueprint(name):
    return jsonify(get_by_name(BlueprintModel, name).to_json())


@api.get('/hardware')
def list_hardware():
    hardware = db.session.execute(db.select(Hardware).order_by(Hardware.name)).scalars()
    return jsonify([{'name': item.name,
                     'description': item.description,
                     'blueprint': item.blueprint.name,
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


# Writes: admins and researchers. Deletes: admins

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
@requires_role(*EDITORS)
def put_blueprint(name):
    try:
        item, created = editing.save_blueprint(key_of(name), json_body(), identity=current_identity())
    except ValidationError as error:
        unprocessable(validation_errors(error))
    db.session.commit()
    return saved(item.to_json(), created)


@api.put('/hardware/<name>')
@requires_role(*EDITORS)
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
@requires_role(*EDITORS)
def put_calibration(sensor_id):
    try:
        item, created = editing.save_calibration(key_of(sensor_id), json_body(), identity=current_identity())
    except ValidationError as error:
        unprocessable(validation_errors(error))
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


@api.get('/<any(blueprints, hardware, calibrations):kinds>/<key>/revisions')
def get_revisions(kinds, key):
    kind = {'blueprints': 'blueprint', 'hardware': 'hardware', 'calibrations': 'calibration'}[kinds]
    return jsonify([revision.to_json() for revision in editing.revisions(kind, key_of(key))])
