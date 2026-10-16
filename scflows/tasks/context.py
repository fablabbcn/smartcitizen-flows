''' What a run used, for its log and its page: device, hardware, blueprint, calibrations, storage '''
import logging
from contextlib import contextmanager

from flask import has_app_context

from scflows import db
from scflows.identity import hardware_name
from scflows.models import Calibration, Hardware
from scflows.runlog import log_line


def iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else (str(value) if value is not None else None)


def device_hardware(device):
    ''' The hardware of the device in flows, or None (also outside the app, e.g. running a task by hand) '''
    postprocessing = getattr(device.handler.json, 'postprocessing', None)
    name = hardware_name(postprocessing.hardware_url) if postprocessing is not None else None
    if not has_app_context():
        return None
    return db.session.execute(db.select(Hardware).filter_by(name=name)).scalar_one_or_none() if name else None


def hardware_context(hardware):
    ''' Versions of the hardware with their sensor ids, and the ids without calibration in flows '''
    if hardware is None:
        return {}
    calibrated = set(db.session.execute(db.select(Calibration.sensor_id)
                                        .where(Calibration.sensor_id.in_(hardware.sensor_ids))).scalars())
    return {'hardware': hardware.name, 'blueprints': [blueprint.name for blueprint in hardware.blueprints],
            'versions': [version.to_json() for version in hardware.versions],
            'uncalibrated': [sensor_id for sensor_id in hardware.sensor_ids if sensor_id not in calibrated]}


def device_context(device, hardware=None):
    ''' The device as the run saw it, from the Smart Citizen API and flows '''
    data = device.handler.json
    postprocessing = getattr(data, 'postprocessing', None)
    context = {'device_name': getattr(data, 'name', None), 'last_reading_at': iso(getattr(data, 'last_reading_at', None)),
               'hardware_url': getattr(postprocessing, 'hardware_url', None),
               'latest_postprocessing': iso(getattr(postprocessing, 'latest_postprocessing', None))}
    context.update(hardware_context(hardware if hardware is not None else device_hardware(device)))
    return context


def describe(context):
    ''' Log lines of a device context '''
    lines = [f"Device {context.get('device_name') or '(no name)'}, last reading {context.get('last_reading_at')}"]
    if context.get('hardware'):
        lines.append(f"Hardware {context['hardware']} (blueprints {', '.join(context['blueprints']) or 'none'})")
        for version in context['versions']:
            ids = ', '.join(f'{slot}={sensor_id}' for slot, sensor_id in version['ids'].items()) or 'no sensor ids'
            lines.append(f"  version {version['from'] or '…'} to {version['to'] or 'now'}: {ids}")
        if context['uncalibrated']:
            lines.append(f"Sensor ids without calibration in flows: {', '.join(context['uncalibrated'])}")
    else:
        lines.append(f"No hardware in flows (hardware_url {context.get('hardware_url')!r})")
    return lines


# Handlers of the tasks running, innermost last (a long run backs the device up first)
ACTIVE = []


class TaskLogHandler(logging.Handler):
    ''' Appends the records of a library to the log of a task, in order with the task's own lines '''
    def __init__(self, task_log, level):
        super().__init__(level)
        self.task_log = task_log
        # A message repeated (e.g. once per channel) is kept once, at its first time, with a count:
        # {unstamped line: [position, count, line]}
        self.seen = {}

    def emit(self, record):
        # Only to the innermost task: the outer one gets its log afterwards
        if not ACTIVE or ACTIVE[-1] is not self:
            return
        message = record.getMessage().strip()
        if record.exc_info and record.exc_info[1] is not None:
            message += f' ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})'
        key = f'{record.levelname.lower()}: [{record.name.split(".")[0]}] {message}'
        if key in self.seen:
            self.seen[key][1] += 1
            return
        line = log_line(record.levelname.lower(), f'[{record.name.split(".")[0]}] {message}', record.created)
        self.seen[key] = [len(self.task_log), 1, line]
        self.task_log.append(line)

    def count_repeats(self):
        for position, count, line in self.seen.values():
            if count > 1 and position < len(self.task_log) and self.task_log[position] == line:
                self.task_log[position] = f'{line} ({count} times)'


@contextmanager
def library_warnings(task_log, names=('scdata', 'smartcitizen_connector'), level=logging.WARNING):
    '''
    Warnings and errors of scdata and the Smart Citizen API client go to the log of the task: they say
    why a load or a post failed
    '''
    handler = TaskLogHandler(task_log, level)
    loggers = [logging.getLogger(name) for name in names]
    previous = [(item.disabled, item.level) for item in loggers]
    for item in loggers:
        # Logging configurations (e.g. alembic's) can disable a logger or raise its level
        item.disabled = False
        if not item.level or item.level > level:
            item.setLevel(level)
        item.addHandler(handler)
    ACTIVE.append(handler)
    try:
        yield
    finally:
        ACTIVE.remove(handler)
        handler.count_repeats()
        for item, (disabled, item_level) in zip(loggers, previous):
            item.removeHandler(handler)
            item.disabled = disabled
            item.setLevel(item_level)
