''' Web interface for hardware, calibrations and sensor names

Admins edit them, with the same checks and history as the API. Researchers see the
metadata of their devices and the sensor names, read only (see access.py). Forms are plain html (no javascript needed).
'''
import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError

from . import db, editing
from .access import can_see, visible_calibrations, visible_hardware
from .api import KEY_PATTERN
from .auth import admin_required, requires_ui_role
from .forms import protect
from .identity import EDITORS
from .models import Blueprint as BlueprintModel, Calibration, ParameterSet, SensorName
from .parameters import sensor_types
from .names import used_by_blueprints
from .schemas import AfeCalibration, AlphasenseCalibration
from .validation import check_hardware

ui = Blueprint('ui', __name__, url_prefix='/metadata')
protect(ui)

editors_required = requires_ui_role(*EDITORS)

CALIBRATION_FIELDS = {
    Calibration.ALPHASENSE_SENSOR: list(AlphasenseCalibration.model_fields),
    Calibration.AFE_BOARD: list(AfeCalibration.model_fields),
}
OPTIONAL_CALIBRATION_FIELDS = {name for schema in (AlphasenseCalibration, AfeCalibration)
                               for name, info in schema.model_fields.items() if not info.is_required()}


def check_visible(kind, key):
    if not can_see(current_user.identity, kind, key):
        abort(403, 'Researchers can only see the hardware of their devices and the calibrations of its sensors.')


def get_or_404(kind, key):
    ''' The item, if it exists and the user can see it. Only admins can change it (POST) '''
    check_visible(kind, key)
    if request.method == 'POST' and not current_user.is_admin:
        abort(403, 'Only admins can change metadata.')
    item = editing.find(kind, key)
    if item is None:
        abort(404)
    return item


def valid_key(key):
    return bool(KEY_PATTERN.match(key or ''))


@ui.get('/')
@editors_required
def index():
    identity = current_user.identity
    names = db.session.execute(db.select(SensorName).order_by(SensorName.position)).scalars().all()
    parameter_sets = {item.name: item for item in db.session.execute(db.select(ParameterSet)).scalars()}
    return render_template('metadata/index.html', hardware=visible_hardware(identity),
                           sensor_types=sensor_types(), parameter_sets=parameter_sets,
                           calibrations=visible_calibrations(identity), names=names, used=used_by_blueprints(),
                           shared_ids=shared_ids(names))


def shared_ids(names):
    ''' Sensor ids with several names: scdata uses the first one '''
    by_id = {}
    for item in names:
        if item.sensor_id:
            by_id.setdefault(item.sensor_id, []).append(item.name)
    return {sensor_id: items for sensor_id, items in by_id.items() if len(items) > 1}


# Hardware

def hardware_from_form(form):
    ''' Hardware description (as sent to the API) from the form. Slots are "SLOT=sensor id" lines '''
    versions = []
    for index in range(int(form.get('version_count', 0))):
        if form.get(f'version_{index}_remove'):
            continue
        ids = {}
        for line in form.get(f'version_{index}_ids', '').splitlines():
            if not line.strip():
                continue
            slot, _, sensor_id = line.partition('=')
            ids[slot.strip()] = sensor_id.strip()
        versions.append({'ids': ids,
                         'from': form.get(f'version_{index}_from') or None,
                         'to': form.get(f'version_{index}_to') or None})
    return {'blueprints': [name for name in form.getlist('blueprints') if name],
            'parameters': json_field(form.get('parameters')),
            'description': form.get('description') or None,
            'comment': form.get('comment') or None,
            'forwarding': form.get('forwarding') or None,
            'versions': versions}


def json_field(value):
    ''' A JSON form field: None when empty, the text itself when it is not valid JSON (validation reports it) '''
    if not (value or '').strip():
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value


def render_hardware(name, data, check=None, new=False):
    blueprints = db.session.execute(db.select(BlueprintModel).order_by(BlueprintModel.name)).scalars().all()
    # Hardware as read from flows has its list; a new one starts with the processing blueprint
    if 'blueprints' not in data:
        data = dict(data, blueprints=[data['blueprint']] if data.get('blueprint') else [])
    return render_template('metadata/hardware.html', name=name, data=data, check=check, new=new,
                           blueprints=blueprints, readonly=not current_user.is_admin)


@ui.route('/hardware/new', methods=['GET', 'POST'])
@admin_required
def new_hardware():
    if request.method == 'GET':
        return render_hardware('', {'versions': [{'ids': {}, 'from': None, 'to': None}]}, new=True)
    return save_hardware_form(request.form.get('name', '').strip(), new=True)


@ui.route('/hardware/<name>', methods=['GET', 'POST'])
@editors_required
def edit_hardware(name):
    item = get_or_404('hardware', name)
    if request.method == 'GET':
        return render_hardware(name, item.to_json())
    return save_hardware_form(name)


def save_hardware_form(name, new=False):
    data = hardware_from_form(request.form)
    action = request.form.get('action')
    if action == 'add_version':
        data['versions'].append({'ids': {}, 'from': None, 'to': None})
        return render_hardware(name, data, new=new)

    check = check_hardware(data)
    if new and not valid_key(name):
        check.errors.insert(0, 'name: letters, numbers, "_" and "-" only (64 characters at most)')
    elif new and editing.find('hardware', name) is not None:
        check.errors.insert(0, f'name: {name} already exists')
    if action != 'save' or not check.valid:
        return render_hardware(name, data, check, new=new)

    editing.save_hardware(name, check.hardware, identity=current_user.identity)
    db.session.commit()
    flash(f'Hardware {name} saved' + (f' with {len(check.warnings)} warnings' if check.warnings else ''))
    return redirect(url_for('ui.edit_hardware', name=name))


# Calibrations

def calibration_from_form(form, kind):
    ''' Calibration data from the form: numbers are stored as numbers, empty values as "" '''
    data = {}
    for field in CALIBRATION_FIELDS[kind]:
        value = form.get(field, '').strip()
        if value == '' and field in OPTIONAL_CALIBRATION_FIELDS:
            continue
        try:
            data[field] = int(value) if value.lstrip('-').isdigit() else float(value)
        except ValueError:
            data[field] = value
    return data


def render_calibration(sensor_id, kind, data, errors=None, new=False):
    return render_template('metadata/calibration.html', sensor_id=sensor_id, kind=kind, data=data,
                           fields=CALIBRATION_FIELDS[kind], optional=OPTIONAL_CALIBRATION_FIELDS,
                           kinds=list(CALIBRATION_FIELDS), errors=errors or [], new=new,
                           readonly=not current_user.is_admin)


@ui.route('/calibrations/new', methods=['GET', 'POST'])
@admin_required
def new_calibration():
    kind = request.values.get('kind', Calibration.ALPHASENSE_SENSOR)
    if kind not in CALIBRATION_FIELDS:
        abort(400)
    if request.method == 'GET' or request.form.get('action') == 'change_kind':
        return render_calibration(request.values.get('sensor_id', ''), kind, {}, new=True)
    return save_calibration_form(request.form.get('sensor_id', '').strip(), kind, new=True)


@ui.route('/calibrations/<sensor_id>', methods=['GET', 'POST'])
@editors_required
def edit_calibration(sensor_id):
    item = get_or_404('calibration', sensor_id)
    if request.method == 'GET':
        return render_calibration(sensor_id, item.kind, item.data)
    return save_calibration_form(sensor_id, item.kind)


def save_calibration_form(sensor_id, kind, new=False):
    data = calibration_from_form(request.form, kind)
    errors = []
    if new and not valid_key(sensor_id):
        errors.append('sensor id: letters, numbers, "_" and "-" only (64 characters at most)')
    elif new and editing.find('calibration', sensor_id) is not None:
        errors.append(f'sensor id: {sensor_id} already exists')
    if not errors:
        try:
            editing.save_calibration(sensor_id, data, identity=current_user.identity)
        except ValidationError as error:
            db.session.rollback()
            errors = [f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
                      for item in error.errors()]
    if errors:
        return render_calibration(sensor_id, kind, data, errors, new=new)
    db.session.commit()
    flash(f'Calibration {sensor_id} saved')
    return redirect(url_for('ui.edit_calibration', sensor_id=sensor_id))


# Sensor names

NAME_FIELDS = ('id', 'description', 'unit')


def render_name(name, data, errors=None, new=False):
    names = db.session.execute(db.select(SensorName).order_by(SensorName.position)).scalars().all()
    others = [item.name for item in names if item.name != name and data.get('id') and item.sensor_id == data.get('id')]
    return render_template('metadata/name.html', name=name, data=data, errors=errors or [], new=new,
                           readonly=not current_user.is_admin, used=used_by_blueprints().get(name, []),
                           others=others, first=next((item.name for item in names if data.get('id')
                                                      and item.sensor_id == data.get('id')), None))


@ui.route('/names/new', methods=['GET', 'POST'])
@admin_required
def new_name():
    if request.method == 'GET':
        return render_name('', {'id': 0, 'description': '', 'unit': ''}, new=True)
    return save_name_form(request.form.get('name', '').strip(), new=True)


@ui.route('/names/<name>', methods=['GET', 'POST'])
@editors_required
def edit_name(name):
    item = get_or_404('name', name)
    if request.method == 'GET':
        return render_name(name, item.to_json())
    return save_name_form(name)


def save_name_form(name, new=False):
    data = {field: request.form.get(field, '').strip() for field in NAME_FIELDS}
    errors = []
    if new and not valid_key(name):
        errors.append('name: letters, numbers, "_" and "-" only (64 characters at most)')
    elif new and editing.find('name', name) is not None:
        errors.append(f'name: {name} already exists')
    if not errors:
        try:
            editing.save_name(name, data, identity=current_user.identity)
        except ValidationError as error:
            db.session.rollback()
            errors = [f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
                      for item in error.errors()]
    if errors:
        data['id'] = int(data['id']) if data['id'].isdigit() else data['id']
        return render_name(name, data, errors, new=new)
    db.session.commit()
    flash(f'Name {name} saved')
    return redirect(url_for('ui.edit_name', name=name))


# Long processing parameters per sensor type

def render_parameters(name, channels, description, errors=None):
    item = editing.find('parameters', name)
    return render_template('metadata/parameters.html', name=name, channels=channels, description=description,
                           errors=errors or [], exists=item is not None, readonly=not current_user.is_admin)


@ui.route('/parameters/<name>', methods=['GET', 'POST'])
@editors_required
def edit_parameters(name):
    if name not in sensor_types():
        abort(404)
    if request.method == 'GET':
        item = editing.find('parameters', name)
        return render_parameters(name, json.dumps(item.channels, indent=2) if item else '{}',
                                 item.description if item else '')
    if not current_user.is_admin:
        abort(403, 'Only admins can change metadata.')
    text, description = request.form.get('channels', ''), request.form.get('description', '').strip()
    try:
        editing.save_parameter_set(name, {'channels': json.loads(text or '{}'), 'description': description or None},
                                   identity=current_user.identity)
    except ValueError as error:
        db.session.rollback()
        message = (f"{'.'.join(str(part) for part in error.errors()[0]['loc'])}: {error.errors()[0]['msg']}"
                   if isinstance(error, ValidationError) else f'channels: not valid JSON ({error})')
        return render_parameters(name, text, description, [message])
    db.session.commit()
    flash(f'Parameters of {name} saved')
    return redirect(url_for('ui.edit_parameters', name=name))


# History and deletion

KIND_LABELS = {'hardware': 'Hardware', 'calibration': 'Calibration', 'name': 'Name', 'parameters': 'Parameters'}


@ui.get('/<any(hardware, calibration, name, parameters):kind>/<key>/history')
@editors_required
def history(kind, key):
    # Also for deleted items
    check_visible(kind, key)
    return render_template('metadata/history.html', kind=kind, label=KIND_LABELS[kind], key=key,
                           revisions=[(revision, changed_fields(revision)) for revision in editing.revisions(kind, key)])


def changed_fields(revision):
    ''' Top level fields that differ between the content before and after a revision '''
    before, after = revision.before or {}, revision.after or {}
    return sorted(field for field in before.keys() | after.keys() if before.get(field) != after.get(field))


@ui.post('/<any(hardware, calibration, name, parameters):kind>/<key>/delete')
@admin_required
def delete(kind, key):
    if not editing.delete(kind, key, identity=current_user.identity):
        abort(404)
    db.session.commit()
    flash(f'{KIND_LABELS[kind]} {key} deleted')
    return redirect(url_for('ui.index') + {'hardware': '#hardware', 'calibration': '#calibrations', 'name': '#names',
                                           'parameters': '#parameters'}[kind])
