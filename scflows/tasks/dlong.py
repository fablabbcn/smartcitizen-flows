''' Long processing: the long blueprint of a device's hardware over a long window of its backups

Baselines (CO2 with ALS, electrochemical sensors with alphasense_als) need months of data, which
cannot be requested from the Smart Citizen API on every run. The backups in S3 are the source:
the run backs the device up first, reads the window (window_days of the blueprint, from the first
day of that month), processes it with the long blueprint filled with the hardware, runs its health
checks and stores the result in S3 (see scflows.storage). Nothing is posted to the Smart Citizen API.
'''
from datetime import datetime, timedelta, timezone

import scdata as sc

from scflows import db, locks
from scflows.custom_logger import logger
from scflows.identity import hardware_name
from scflows.models import Blueprint, Hardware
from scflows.storage import month_start, root, write_processed
from scflows.tasks.dprocess import health_checks
from scflows.tools import refresh_metadata

DEFAULT_WINDOW_DAYS = 90


def long_blueprint(device):
    ''' The long blueprint of the device's hardware in flows, or None '''
    postprocessing = device.handler.json.postprocessing
    name = hardware_name(postprocessing.hardware_url) if postprocessing is not None else None
    hardware = db.session.execute(db.select(Hardware).filter_by(name=name)).scalar_one_or_none() if name else None
    return hardware.blueprint_of(Blueprint.LONG) if hardware else None


async def back_up(device_id, logger_handler, task_log):
    ''' Brings the backup up to date, unless a backup of the device is already running '''
    from scflows.tasks.dbackup import dbackup

    # Backups are only written to S3: with a local storage (STORAGE_ROOT, e.g. tests) they are used as they are
    if not root().startswith('s3://'):
        task_log.append(logger_handler(f'Local storage {root()}: using the backups as they are', 'warning'))
        return True

    key = f'scflows:lock:backup:{device_id}'
    token = locks.acquire(key)
    if token is None:
        task_log.append(logger_handler('A backup of the device is running: using the backups as they are', 'warning'))
        return True
    try:
        backup_log, backup_state = await dbackup(device_id)
    finally:
        locks.release(key, token)
    task_log += [f'backup {line}' for line in backup_log]
    # No new data is fine: the backup is up to date
    return backup_state[0] == 'SUCCESS' or backup_state[1] in ('NO_NEW_DATA', 'EMPTY_DATA')


async def dlong(device_id, dry_run=False):
    task_log = []

    def logger_handler(msg, level='info'):
        getattr(logger, level)(msg)
        return f'{level}: {msg}'

    def done(state, message, health=None):
        task_log.append(logger_handler(f'Concluded long job for {device_id}'))
        return task_log, [state, message], health

    logger_handler(f'Long processing instance for device {device_id}')
    refresh_metadata()

    if not await back_up(device_id, logger_handler, task_log):
        task_log.append(logger_handler('The backup failed: not processing', 'error'))
        return done('ABORTED', 'BACKUP_FAILED')

    device = sc.Device(params=sc.APIParams(id=device_id))
    blueprint = long_blueprint(device)
    if blueprint is None:
        task_log.append(logger_handler(f'Device {device_id} has no long blueprint in its hardware', 'error'))
        return done('ABORTED', 'NO_LONG_BLUEPRINT')
    device.use_blueprint(blueprint.name, blueprint.body)
    task_log.append(logger_handler(f'Using blueprint {blueprint.name}'))

    window_days = blueprint.meta.get('window_days') or DEFAULT_WINDOW_DAYS
    end = datetime.now(timezone.utc)
    start = month_start(end - timedelta(days=window_days)).to_pydatetime()
    sensors = device.required_sensors
    task_log.append(logger_handler(f'Window: {start:%Y-%m-%d} to {end:%Y-%m-%d}. Sensors: {sensors}'))
    if not sensors:
        task_log.append(logger_handler('The blueprint needs no sensors of this device', 'error'))
        return done('ABORTED', 'NO_SENSORS_TO_LOAD')

    if not device.load_from_storage(min_date=start, max_date=end, channels=sensors, root=root()) or device.data.empty:
        task_log.append(logger_handler('No data in the backups for the window', 'warning'))
        return done('ABORTED', 'EMPTY_DATA')
    task_log.append(logger_handler(f'Loaded {len(device.data)} rows from the backups'))

    processed = device.process()
    health = health_checks(device, logger_handler, task_log)
    if not processed:
        return done('ABORTED', 'PROCESSING_FAILED', health)

    channels = [channel.name for channel in device.channels if channel.name in device.data.columns]
    if dry_run:
        task_log.append(logger_handler(f'Dry run: {len(channels)} channels not stored'))
        return done('SUCCESS', 'PROCESSED (DRY RUN)', health)

    info = {'blueprint': blueprint.name, 'start': start.isoformat(), 'end': end.isoformat(),
            'rows': len(device.data), 'channels': channels,
            # The parameters used, to tell results apart when they are tuned
            'parameters': {channel.name: channel.kwargs for channel in device.channels}}
    try:
        months = write_processed(device_id, blueprint.name, device.data[channels], info)
    except Exception as error:
        task_log.append(logger_handler(f'Storing the result failed: {type(error).__name__}: {error}', 'error'))
        return done('FAILED', 'STORAGE_FAILED', health)
    task_log.append(logger_handler(f'Stored {len(channels)} channels, months {", ".join(months)}'))
    return done('SUCCESS', 'PROCESSED AND STORED', health)
