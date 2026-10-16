''' Long processing: the long blueprint of a device's hardware over a long window of its backups

Baselines (CO2 with ALS, electrochemical sensors with alphasense_als) need months of data, which
cannot be requested from the Smart Citizen API on every run. The backups in S3 are the source:
the run backs the device up first, reads the window (window_days of the blueprint, from the first
day of that month), processes it with the long blueprint filled with the hardware, runs its health
checks and stores the result in S3 (see scflows.storage). Nothing is posted to the Smart Citizen API.
'''
from datetime import datetime, timedelta, timezone

import scdata as sc

from scflows import locks
from scflows.custom_logger import logger
from scflows.models import Blueprint
from scflows.parameters import apply_parameters
from scflows.storage import month_start, root, write_processed
from scflows.runlog import log_line, tagged
from scflows.tasks.context import describe, device_context, device_hardware, iso, library_warnings
from scflows.tasks.dprocess import health_checks, period
from scflows.tools import refresh_metadata

DEFAULT_WINDOW_DAYS = 90


async def back_up(device_id, logger_handler, task_log, context=None):
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
        # Without waiting for the backup period to end: the window ends now
        backup_log, backup_state = await dbackup(device_id, up_to_date=True,
                                                 context=context if context is not None else {})
    finally:
        locks.release(key, token)
    task_log += [tagged(line, 'backup') for line in backup_log]
    # No new data is fine: the backup is up to date
    return backup_state[0] == 'SUCCESS' or backup_state[1] in ('NO_NEW_DATA', 'EMPTY_DATA')


async def dlong(device_id, dry_run=False, context=None):
    ''' context (a dict) gets what the run used: device, hardware, blueprint, window, storage '''
    task_log = []
    context = {} if context is None else context
    # Why a read or a write failed is in the warnings of scdata and of the Smart Citizen API client
    with library_warnings(task_log):
        try:
            return await long_device(device_id, dry_run, task_log, context)
        except Exception as error:
            # The run keeps the log up to the error
            logger.exception(f'Long processing of device {device_id} raised')
            task_log.append(log_line('error', f'{type(error).__name__}: {error}'))
            return task_log, ['FAILED', f'{type(error).__name__}: {error}'], None


async def long_device(device_id, dry_run, task_log, context):

    def logger_handler(msg, level='info'):
        getattr(logger, level)(msg)
        return log_line(level, msg)

    def done(state, message, health=None):
        task_log.append(logger_handler(f'Concluded long job for {device_id}'))
        return task_log, [state, message], health

    logger_handler(f'Long processing instance for device {device_id}')
    refresh_metadata()

    context['backup'] = {}
    if not await back_up(device_id, logger_handler, task_log, context['backup']):
        task_log.append(logger_handler('The backup failed: not processing', 'error'))
        return done('ABORTED', 'BACKUP_FAILED')

    device = sc.Device(params=sc.APIParams(id=device_id))
    hardware = device_hardware(device)
    context.update(device_context(device, hardware))
    for line in describe(context):
        task_log.append(logger_handler(line))
    blueprint = hardware.blueprint_of(Blueprint.LONG) if hardware else None
    if blueprint is None:
        task_log.append(logger_handler(f'Device {device_id} has no long blueprint in its hardware', 'error'))
        return done('ABORTED', 'NO_LONG_BLUEPRINT')
    device.use_blueprint(blueprint.name, blueprint.body)
    # Parameters of its sensor types, then of its hardware
    parameter_sets = apply_parameters(device, hardware)
    context.update(blueprint=blueprint.name, parameter_sets=parameter_sets,
                   storage=f'{root()}/devices/{device_id}/data/',
                   results=f'{root()}/devices/{device_id}/processed/{blueprint.name}/')
    task_log.append(logger_handler(f'Using blueprint {blueprint.name}, parameters of: '
                                   f'{", ".join(parameter_sets) or "the blueprint only"}'
                                   f'{" and hardware " + hardware.name if hardware.parameters else ""}'))

    window_days = blueprint.meta.get('window_days') or DEFAULT_WINDOW_DAYS
    end = datetime.now(timezone.utc)
    start = month_start(end - timedelta(days=window_days)).to_pydatetime()
    sensors = device.required_sensors
    context.update(start=iso(start), end=iso(end), sensors=list(sensors or []))
    task_log.append(logger_handler(f"Window: {start:%Y-%m-%d} to {end:%Y-%m-%d}, from {context['storage']}. Sensors: {sensors}"))
    if not sensors:
        task_log.append(logger_handler('The blueprint needs no sensors of this device', 'error'))
        return done('ABORTED', 'NO_SENSORS_TO_LOAD')

    if not device.load_from_storage(min_date=start, max_date=end, channels=sensors, root=root()) or device.data.empty:
        task_log.append(logger_handler('No data in the backups for the window', 'warning'))
        return done('ABORTED', 'EMPTY_DATA')
    context['rows'] = len(device.data)
    task_log.append(logger_handler(f'Loaded from the backups: {period(device.data)}'))

    # A channel graph with a cycle raises: the checks run anyway
    error = None
    try:
        processed = device.process()
    except Exception as exception:
        error = f'{type(exception).__name__}: {exception}'
        task_log.append(logger_handler(f'Processing failed: {error}', 'error'))
        processed = False
    health = health_checks(device, logger_handler, task_log)
    if error:
        return done('FAILED', f'PROCESSING_ERROR: {error}', health)
    if not processed:
        return done('ABORTED', 'PROCESSING_FAILED', health)

    channels = [channel.name for channel in device.channels if channel.name in device.data.columns]
    context['channels'] = channels
    task_log.append(logger_handler(f'Processed {len(channels)} channels ({", ".join(channels)})'))
    if dry_run:
        task_log.append(logger_handler(f'Dry run: {len(channels)} channels not stored'))
        return done('SUCCESS', 'PROCESSED (DRY RUN)', health)

    info = {'blueprint': blueprint.name, 'start': start.isoformat(), 'end': end.isoformat(),
            'rows': len(device.data), 'channels': channels, 'parameter_sets': parameter_sets,
            'hardware_parameters': hardware.parameters or {},
            # The parameters used, to tell results apart when they are tuned
            'parameters': {channel.name: channel.kwargs for channel in device.channels}}
    try:
        months = write_processed(device_id, blueprint.name, device.data[channels], info)
    except Exception as error:
        task_log.append(logger_handler(f'Storing the result failed: {type(error).__name__}: {error}', 'error'))
        return done('FAILED', 'STORAGE_FAILED', health)
    context['months'] = months
    task_log.append(logger_handler(f"Stored {len(channels)} channels in {context['results']}, months {', '.join(months)}"))
    return done('SUCCESS', 'PROCESSED AND STORED', health)
