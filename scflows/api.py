''' Public read API for processing metadata

Paths follow the layout of the smartcitizen-data repository, so that
{base}/hardware/<name>.json, {base}/blueprints/<name>.json and
{base}/calibrations/calibrations.json work with base = <host>/api/v1/
'''
from flask import Blueprint, abort, current_app, jsonify, request, url_for
from werkzeug.exceptions import HTTPException

from . import db
from .models import Blueprint as BlueprintModel, Calibration, Hardware

api = Blueprint('api', __name__, url_prefix='/api/v1')

CACHE_CONTROL = 'public, max-age=300'


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
    return jsonify({'error': error.name, 'message': error.description}), error.code


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
    return jsonify([{'name': blueprint.name,
                     'url': url_for('api.get_blueprint', name=f'{blueprint.name}.json', _external=True)}
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
                     'url': url_for('api.get_hardware', name=f'{item.name}.json', _external=True)}
                    for item in hardware])


@api.get('/hardware/<name>')
def get_hardware(name):
    return jsonify(get_by_name(Hardware, name).to_json())


@api.get('/calibrations')
@api.get('/calibrations/calibrations.json')
def list_calibrations():
    ''' All calibrations as {sensor_id: data}, optionally filtered with ?kind= '''
    query = db.select(Calibration).order_by(Calibration.sensor_id)
    if 'kind' in request.args:
        query = query.filter_by(kind=request.args['kind'])
    return jsonify({item.sensor_id: item.to_json() for item in db.session.execute(query).scalars()})


@api.get('/calibrations/<sensor_id>')
def get_calibration(sensor_id):
    return jsonify(get_by_name(Calibration, sensor_id, field='sensor_id').to_json())
