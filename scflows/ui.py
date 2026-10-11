''' Web interface to edit hardware and calibrations, for admins and researchers

Uses the same checks and history as the API. Forms are plain html (no javascript needed).
'''
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError

from . import db, editing
from .api import KEY_PATTERN
from .auth import admin_required, requires_ui_role
from .forms import protect
from .identity import EDITORS
from .models import Blueprint as BlueprintModel, Calibration, Hardware
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


def get_or_404(kind, key):
    item = editing.find(kind, key)
    if item is None:
        abort(404)
    return item


def valid_key(key):
    return bool(KEY_PATTERN.match(key or ''))


@ui.get('/')
@editors_required
def index():
    hardware = db.session.execute(db.select(Hardware).order_by(Hardware.name)).scalars().all()
    calibrations = db.session.execute(db.select(Calibration).order_by(Calibration.sensor_id)).scalars().all()
    return render_template('metadata/index.html', hardware=hardware, calibrations=calibrations)


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
    return {'blueprint': form.get('blueprint') or None,
            'description': form.get('description') or None,
            'comment': form.get('comment') or None,
            'forwarding': form.get('forwarding') or None,
            'versions': versions}


def render_hardware(name, data, check=None, new=False):
    blueprints = db.session.execute(db.select(BlueprintModel.name).order_by(BlueprintModel.name)).scalars().all()
    return render_template('metadata/hardware.html', name=name, data=data, check=check, new=new,
                           blueprints=blueprints)


@ui.route('/hardware/new', methods=['GET', 'POST'])
@editors_required
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
                           kinds=list(CALIBRATION_FIELDS), errors=errors or [], new=new)


@ui.route('/calibrations/new', methods=['GET', 'POST'])
@editors_required
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


# History and deletion

KIND_LABELS = {'hardware': 'Hardware', 'calibration': 'Calibration'}


@ui.get('/<any(hardware, calibration):kind>/<key>/history')
@editors_required
def history(kind, key):
    return render_template('metadata/history.html', kind=kind, label=KIND_LABELS[kind], key=key,
                           revisions=list(editing.revisions(kind, key)))


@ui.post('/<any(hardware, calibration):kind>/<key>/delete')
@admin_required
def delete(kind, key):
    if not editing.delete(kind, key, identity=current_user.identity):
        abort(404)
    db.session.commit()
    flash(f'{KIND_LABELS[kind]} {key} deleted')
    return redirect(url_for('ui.index'))
