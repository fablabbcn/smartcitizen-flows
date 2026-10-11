''' Web interface for device health: admins see every device, researchers their own '''
from flask import Blueprint, abort, render_template, request
from flask_login import current_user

from . import db, health
from .auth import requires_ui_role
from .identity import EDITORS
from .models import DeviceHealth

health_ui = Blueprint('health_ui', __name__, url_prefix='/health')

editors_required = requires_ui_role(*EDITORS)


@health_ui.get('/')
@editors_required
def index():
    identity = current_user.identity
    items = health.latest(identity)
    # Researchers also see their devices without checks yet (not processed)
    checked = {item.device_id for item in items}
    unchecked = [] if current_user.is_admin else sorted(set(identity.devices) - checked)
    counts = {status: sum(1 for item in items if item.status == status) for status in DeviceHealth.STATUSES}
    status = request.args.get('status') if request.args.get('status') in DeviceHealth.STATUSES else None
    shown = [item for item in items if status is None or item.status == status]
    # Worst first
    shown.sort(key=lambda item: (-DeviceHealth.STATUSES.index(item.status), item.device_id))
    return render_template('health/index.html', items=shown, unchecked=unchecked if status is None else [],
                           counts=counts, total=len(items), status=status,
                           issues={item.id: health.issues(item) for item in shown},
                           timeline=health.recent_statuses([item.device_id for item in shown]),
                           problem_ratio=health.PROBLEM_RATIO, keep_days=health.KEEP_DAYS)


@health_ui.get('/<int:device_id>')
@editors_required
def device(device_id):
    if not health.can_see_device(current_user.identity, device_id):
        abort(403, 'Researchers can only see the health of their devices.')
    items = health.history(device_id)
    if not items:
        abort(404, 'No health checks for this device yet: they run when the device is processed.')
    selected = items[0]
    if request.args.get('record', type=int):
        selected = db.session.get(DeviceHealth, request.args.get('record', type=int))
        if selected is None or selected.device_id != device_id:
            abort(404)
    return render_template('health/device.html', device_id=device_id, items=items, selected=selected,
                           latest=items[0], problem_ratio=health.PROBLEM_RATIO, keep_days=health.KEEP_DAYS)
