import scdata as sc
from pandas import Timedelta
from scdata.tools.date import localise_date

import sys
import asyncio
from scflows.config import config, posting_enabled
from scflows.custom_logger import logger
from scflows.models import Blueprint, Job
from scflows.parameters import apply_parameters, lookback_hours
from scflows.runlog import log_line
from scflows.tasks.context import describe, device_context, device_hardware, iso, library_warnings
from scflows.tools import refresh_metadata


def period(data):
    ''' Rows and time span of the data, for the log '''
    if data is None or data.empty:
        return 'no rows'
    return f'{len(data)} rows, {data.index.min()} to {data.index.max()}'


async def dprocess(device, dry_run = False, context = None):
    '''
        This function processes a device from SC API assuming there
        is postprocessing information in it and that it's valid for doing
        so. context (a dict) gets what the run used: device, hardware, blueprint, period, channels
    '''
    task_log = []
    context = {} if context is None else context
    # Why a load or a post failed is in the warnings of scdata and of the Smart Citizen API client
    with library_warnings(task_log):
        try:
            return await process_device(device, dry_run, task_log, context)
        except Exception as error:
            # The run keeps the log up to the error
            logger.exception(f'Processing device {device} raised')
            task_log.append(log_line('error', f'{type(error).__name__}: {error}'))
            return task_log, ['FAILED', f'{type(error).__name__}: {error}'], None


def posting_summary(handler, dry_run):
    ''' Lines on what the Smart Citizen API client posted (or would post, in a dry run), and what it did not '''
    posted, not_posted = getattr(handler, 'posted', None) or {}, getattr(handler, 'not_posted', None) or {}
    lines = []
    if posted:
        lines.append(('Would post: ' if dry_run else 'Posted: ') + ', '.join(
            f"{name} (sensor {item['id']}, {item['readings']} readings)" for name, item in posted.items()))
    by_reason = {}
    for name, reason in not_posted.items():
        by_reason.setdefault(reason, []).append(name)
    for reason, names in by_reason.items():
        lines.append(f'Not posted ({reason}): {", ".join(names)}')
    return lines


async def process_device(device, dry_run, task_log, context):

    def logger_handler(msg, level='info'):
        if level == 'info':
            logger.info(msg)
        elif level == 'warning':
            logger.warning(msg)
        elif level == 'error':
            logger.error(msg)
        return log_line(level, msg)

    logger_handler(f'Processing instance for device {device}')

    # Results are posted with the token of a Smart Citizen admin: without one (e.g. a local stack), only dry runs
    if not dry_run and not posting_enabled():
        task_log.append(logger_handler('SC_BEARER is not set: posting is disabled. Only dry runs can process devices.', 'error'))
        return task_log, ['ABORTED', 'NO_TOKEN'], None

    # Create device from SC API
    # Changes made in flows (calibrations, blueprints) apply without restarting the worker
    refresh_metadata()

    d = sc.Device(params=sc.APIParams(id=device), blueprint_kind=Blueprint.PROCESS)
    task_state = [None, None]
    health = None
    # Sensors only for the checks, and the period of the new readings to check them on
    check_only, checked, new_readings = [], None, config._max_load_amount

    if d:
        task_log.append(logger_handler(f'Device {device} Initialized'))
        hardware = device_hardware(d)
        context.update(device_context(d, hardware))
        for line in describe(context):
            task_log.append(logger_handler(line))

        # Only the process blueprint of the hardware in flows, as stored here
        blueprint = hardware.blueprint_of(Blueprint.PROCESS) if hardware else None
        if blueprint is None:
            task_log.append(logger_handler(f'Device {device} has no process blueprint in its hardware', 'error'))
            task_log.append(logger_handler(f'Concluded job for {device}'))
            return task_log, ['ABORTED', 'NO_PROCESS_BLUEPRINT'], None
        d.use_blueprint(blueprint.name, blueprint.body)
        # Process parameters of its hardware
        apply_parameters(d, hardware, Blueprint.PROCESS)
        context['blueprint'] = d.blueprint
        context['process_parameters'] = hardware.process_parameters or {}
        task_log.append(logger_handler(f'Blueprint {d.blueprint}'
                                       f'{", parameters of hardware " + hardware.name if hardware.process_parameters else ""}'))

        # Reprocessing: the first run starts again (from a date, or the first hardware version), not from the latest
        # postprocessing, and posts the new one
        restart = context.get('reprocess') == Job.REPROCESS_PENDING
        previous = context.get('reprocess_from') if restart else d.handler.postprocessing['latest_postprocessing']
        if restart:
            task_log.append(logger_handler(f'Reprocessing from {previous or "the first hardware version"}'))
        since = localise_date(previous, 'UTC') if previous is not None else None
        # Readings before the new ones that channels need (e.g. a baseline): processed, not posted again
        lookback = lookback_hours(hardware.process_parameters) if since is not None else 0
        if since is not None:
            d.options.min_date = (since - Timedelta(hours=lookback)).isoformat() if lookback else previous
            task_log.append(logger_handler(f'Setting min_date as: {d.options.min_date}'
                                           f'{f" ({lookback} hours before the latest postprocessing)" if lookback else ""}'))
        context['lookback_hours'] = lookback

        # Only load the sensors that the blueprint channels need
        d.options.channels = d.required_sensors
        task_log.append(logger_handler(f'Sensors to load: {d.options.channels}'))
        # The sensors that only the checks name are loaded after posting, for the new readings
        check_only = [sensor for sensor in check_sensors(d, hardware) if sensor not in d.options.channels]
        context['check_sensors'] = check_only
        if check_only:
            task_log.append(logger_handler(f'Sensors for the checks only: {check_only}'))

        # The readings of the lookback (one a minute) do not count against the limit of new ones
        new_readings = config._reprocess_load_amount if context.get('reprocess') else config._max_load_amount
        d.options.limit = new_readings + int(lookback * 60)
        context.update(min_date=iso(d.options.min_date), sensors=list(d.options.channels or []), limit=d.options.limit)

        if not d.options.channels:
            # An empty list would make the handler load all sensors
            task_log.append(logger_handler(f'Device {device} has no sensors needed by its blueprint', 'error'))
            task_state = ['ABORTED', 'NO_SENSORS_TO_LOAD']
        elif d.valid_for_processing:
            task_log.append(logger_handler(f'Device is valid for processing. Loading from {d.options.min_date} '
                                           f'(at most {d.options.limit} readings)'))

            if await d.load():
                task_log.append(logger_handler(f'Device was loaded: {period(d.data)}'))
                context.update(rows=len(d.data), loaded_from=iso(d.data.index.min()), loaded_to=iso(d.data.index.max()))
                if not d.data.empty:
                    checked = (since if lookback else d.data.index.min(), d.data.index.max())

                # Process it (a channel graph with a cycle raises): the checks run anyway
                error = None
                try:
                    processed = d.process()
                except Exception as exception:
                    error = f'{type(exception).__name__}: {exception}'
                    task_log.append(logger_handler(f'Processing failed: {error}', 'error'))
                    processed = False
                # Checks of the blueprint (gaps, implausible, flat values, outliers), stored by flows
                health = health_checks(d, logger_handler, task_log)
                if processed and lookback:
                    # The readings of the lookback were posted by earlier runs. The latest postprocessing is the
                    # last posted reading plus the frequency: a reading then is new
                    d.data = d.data[d.data.index >= since]
                    task_log.append(logger_handler(f'New readings after the lookback: {period(d.data)}'))
                if processed and d.data.empty:
                    task_log.append(logger_handler(f'Device {device} has no readings from {iso(since)}', 'warning'))
                    task_state = ['ABORTED', 'NO_NEW_DATA']
                elif processed:
                    channels = [channel.name for channel in getattr(d, 'channels', []) if channel.name in d.data.columns]
                    context['channels'] = channels
                    task_log.append(logger_handler(f'Device was processed: {len(channels)} channels ({", ".join(channels)})'))

                    # Update postprocessing date
                    d.update_postprocessing_date()
                    latest = iso(getattr(d.handler, 'latest_postprocessing', None))
                    context['new_latest_postprocessing'] = latest
                    # More readings to process than a run loads (e.g. reprocessing)
                    last_reading = getattr(d.handler.json, 'last_reading_at', None)
                    context['behind'] = bool(latest and last_reading and localise_date(latest, 'UTC')
                                             < localise_date(last_reading, 'UTC') - Timedelta(hours=1))
                    task_log.append(logger_handler(f'Latest postprocessing: {context.get("latest_postprocessing")} -> {latest}'))

                    # Post results
                    if d.postprocessing_updated:
                        task_log.append(logger_handler(f'Posting {len(channels)} channels{" (dry run)" if dry_run else ""}'))
                        posted = await d.post(columns = 'channels', dry_run=dry_run, max_retries=3, with_postprocessing=True)
                        for line in posting_summary(d.handler, dry_run):
                            task_log.append(logger_handler(line))
                        if posted:
                            task_log.append(logger_handler(f'Dry run: nothing was posted for device {device}' if dry_run
                                                           else f'Device {device} was posted'))
                            task_state = ['SUCCESS', 'PROCESSED AND UPLOADED']
                        else:
                            task_log.append(logger_handler(f'Device {device} was not posted (see the warnings above)', 'error'))
                            task_state = ['FAILED', 'DATA_POSTING_FAILED']
                    else:
                        task_log.append(logger_handler(f'Device {device} was not posted', 'warning'))
                        task_state = ['ABORTED', 'POSTPROCESSING_UPDATE_FAILED']
                elif error:
                    # An error, as when it failed the celery task: not a device that cannot be processed
                    task_state = ['FAILED', f'PROCESSING_ERROR: {error}']
                else:
                    task_state = ['ABORTED', 'PROCESSING_FAILED']
            else:
                task_log.append(logger_handler(f'Device {device} was not loaded (see the warnings above)', 'warning'))

                if d.data.empty:
                    task_log.append(logger_handler(f'Device {device} data is empty. Nothing to do', 'warning'))
                    task_state = ['ABORTED', 'EMPTY_DATA']
        else:
            task_log.append(logger_handler(f'Device {device} not valid for processing', 'error'))
            task_state = ['ABORTED', 'NOT_VALID_FOR_PROCESSING']
    else:
        task_log.append(logger_handler(f'Device {device} not valid', 'error'))
        task_state = ['ABORTED', 'DEVICE_NOT_VALID']

    if health is not None and check_only and checked is not None:
        health = await with_check_sensors(d, health, check_only, checked, new_readings, logger_handler, task_log)

    task_log.append(logger_handler(f'Concluded job for {device}'))

    return task_log, task_state, health


def used_adcs(hardware):
    ''' ADC channels with a sensor in a version of the hardware: slot AS_48_01 uses ADC_48_0 and ADC_48_1 '''
    adcs = set()
    for version in getattr(hardware, 'versions', None) or []:
        for slot in version.ids or {}:
            parts = slot.split('_')
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                adcs.update(f'ADC_{parts[1]}_{channel}' for channel in parts[2])
    return adcs


def check_sensors(d, hardware=None):
    '''
    Sensors (source names) of the device that the checks of its blueprint name: in their kwargs or args, also
    nested (e.g. the columns of gap_sizes). Not the channels of the blueprint, which processing calculates, nor
    the ADC channels without a sensor in the hardware
    '''
    names = set()

    def collect(value):
        if isinstance(value, str):
            names.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    for check in d.checks or []:
        collect(getattr(check, 'kwargs', None))
        collect(getattr(check, 'args', None))
    sensor_map = d.__sensor_map__()
    channels = {channel.name for channel in getattr(d, 'channels', [])}
    adcs = used_adcs(hardware)
    names = {name for name in names - channels if not name.startswith('ADC_') or name in adcs}
    return sorted({sensor_map[name] for name in names if name in sensor_map})


async def with_check_sensors(d, health, sensors, checked, limit, logger_handler, task_log):
    '''
    Loads the sensors that only the checks need, for the new readings (checked: their first and last time), runs
    the checks on them and adds their columns to the health of the run. The data of the run is kept
    '''
    data, options = d.data, d.options
    saved = {name: getattr(options, name, None) for name in ('channels', 'min_date', 'max_date', 'limit')}
    start, end = checked
    options.channels, options.limit = sensors, limit
    options.min_date, options.max_date = start.isoformat(), end.isoformat()
    try:
        loaded = await d.load()
        extra = d.data if loaded else None
    except Exception as error:
        task_log.append(logger_handler(f'Sensors for the checks not loaded: {type(error).__name__}: {error}', 'warning'))
        extra = None
    finally:
        for name, value in saved.items():
            setattr(options, name, value)
    if extra is None or extra.empty:
        d.data = data
        task_log.append(logger_handler('No readings of the sensors for the checks', 'warning'))
        return health
    task_log.append(logger_handler(f'Sensors for the checks loaded: {period(extra)}'))
    try:
        extra_health = health_checks(d, logger_handler, task_log)
    finally:
        d.data = data
    if extra_health is None:
        return health
    # The columns of each check, by its name
    checks = {check['name']: check for check in health['checks']}
    for check in extra_health['checks']:
        if check['name'] not in checks:
            health['checks'].append(check)
            continue
        checks[check['name']]['columns'].update({column: summary for column, summary in check.get('columns', {}).items()
                                                 if column not in checks[check['name']]['columns']})
        if str(check.get('status', '')).startswith('error') and checks[check['name']].get('status') == 'ok':
            checks[check['name']]['status'] = check['status']
    return health


def health_checks(d, logger_handler, task_log):
    ''' Runs the checks of the blueprint on the data of the run. Returns device.health, or None '''
    if not d.checks:
        return None
    try:
        d.health_checks()
    except Exception as error:
        task_log.append(logger_handler(f'Health checks failed: {type(error).__name__}: {error}', 'warning'))
        return None
    health = getattr(d, 'health', None)
    if not health:
        return None
    task_log.append(logger_handler(f'Health checks done: {len(health["checks"])} checks on {health["rows"]} rows'))
    return dict(health, device_name=getattr(d.handler.json, 'name', None), blueprint=d.blueprint)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Process a device of the Smart Citizen API')
    parser.add_argument('--device', type=int, required=True)
    parser.add_argument('--dry-run', action='store_true', help='Process without posting')
    args = parser.parse_args()

    log, state, _ = asyncio.run(dprocess(args.device, dry_run=args.dry_run))
    logger.info(f'Result: {state}')
