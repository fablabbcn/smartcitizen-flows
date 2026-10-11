''' Device health: the checks of the blueprint (gaps, implausible, flat values, outliers), stored per run

Processing runs the checks of the blueprint on the data of each run (scdata's
device.health). flows stores the result with a status per column, check and device:

- ok: nothing flagged
- warning: part of the readings (or of the time, for gaps) flagged, below PROBLEM_RATIO
- problem: PROBLEM_RATIO or more flagged
- error: the check could not run (e.g. wrong settings in the blueprint)

Records older than KEEP_DAYS are deleted every day; the latest of each device is always kept.
'''
from datetime import datetime, timedelta, timezone

from . import db
from .identity import ADMIN
from .models import DeviceHealth

PROBLEM_RATIO = 0.2
KEEP_DAYS = 30


def now():
    return datetime.now(timezone.utc)


def column_status(summary):
    ratio = summary.get('ratio') or 0
    if ratio >= PROBLEM_RATIO:
        return DeviceHealth.PROBLEM
    return DeviceHealth.WARNING if ratio > 0 else DeviceHealth.OK


def worst(statuses, default=DeviceHealth.OK):
    statuses = list(statuses)
    return max(statuses, key=DeviceHealth.STATUSES.index) if statuses else default


def rate(checks):
    ''' Adds the status of each column and check. Returns the status of the device '''
    for check in checks:
        for summary in check.get('columns', {}).values():
            summary['status'] = column_status(summary)
        if str(check.get('status', '')).startswith('error'):
            check['rating'] = DeviceHealth.ERROR
        else:
            check['rating'] = worst(summary['status'] for summary in check.get('columns', {}).values())
    return worst(check['rating'] for check in checks)


def parse_time(value):
    return datetime.fromisoformat(value) if value else None


def record(device_id, health, run=None):
    '''
    Stores the health of a device (scdata's device.health, plus device_name and blueprint).
    Returns the record, or None if there is nothing to store
    '''
    if not health or not health.get('checks'):
        return None
    checks = health['checks']
    item = DeviceHealth(device_id=device_id, device_name=health.get('device_name'), blueprint=health.get('blueprint'),
                        run=run, start=parse_time(health.get('start')), end=parse_time(health.get('end')),
                        rows=health.get('rows') or 0, status=rate(checks), checks=checks)
    db.session.add(item)
    return item


def device_ids_of(identity):
    ''' Devices whose health the identity can see. None: all '''
    return None if identity.role == ADMIN else set(identity.devices)


def can_see_device(identity, device_id):
    ids = device_ids_of(identity)
    return ids is None or device_id in ids


def latest(identity=None, device_ids=None):
    ''' Latest record of each device the identity can see, ordered by device id '''
    newest = db.select(db.func.max(DeviceHealth.id)).group_by(DeviceHealth.device_id)
    query = db.select(DeviceHealth).where(DeviceHealth.id.in_(newest)).order_by(DeviceHealth.device_id)
    ids = device_ids_of(identity) if identity is not None else device_ids
    if ids is not None:
        query = query.where(DeviceHealth.device_id.in_(ids))
    return db.session.execute(query).scalars().all()


def history(device_id, limit=50):
    ''' Records of a device, newest first '''
    return db.session.execute(db.select(DeviceHealth).filter_by(device_id=device_id)
                              .order_by(DeviceHealth.id.desc()).limit(limit)).scalars().all()


def recent_statuses(device_ids, per_device=12):
    ''' {device_id: [status, ...]} of the latest records, oldest first (for a small timeline) '''
    rows = db.session.execute(
        db.select(DeviceHealth.device_id, DeviceHealth.status, DeviceHealth.id)
        .where(DeviceHealth.device_id.in_(device_ids), DeviceHealth.created_at >= now() - timedelta(days=KEEP_DAYS))
        .order_by(DeviceHealth.id.desc())).all()
    result = {}
    for device_id, status, _ in rows:
        statuses = result.setdefault(device_id, [])
        if len(statuses) < per_device:
            statuses.append(status)
    return {device_id: list(reversed(statuses)) for device_id, statuses in result.items()}


def issues(item, limit=3):
    ''' The worst columns of a record: [(check, column, status, ratio)] '''
    found = [(check['name'], column, summary['status'], summary.get('ratio', 0))
             for check in item.checks for column, summary in check.get('columns', {}).items()
             if summary.get('status') not in (None, DeviceHealth.OK)]
    found += [(check['name'], None, DeviceHealth.ERROR, None) for check in item.checks
              if check.get('rating') == DeviceHealth.ERROR]
    found.sort(key=lambda issue: (-DeviceHealth.STATUSES.index(issue[2]), -(issue[3] or 0)))
    return found[:limit], max(len(found) - limit, 0)


def prune(days=KEEP_DAYS):
    ''' Deletes records older than days, except the latest of each device. Returns how many '''
    newest = db.select(db.func.max(DeviceHealth.id)).group_by(DeviceHealth.device_id)
    result = db.session.execute(db.delete(DeviceHealth).where(
        DeviceHealth.created_at < now() - timedelta(days=days), DeviceHealth.id.not_in(newest)))
    db.session.commit()
    return result.rowcount
