import scdata as sc

import sys
import asyncio
from scflows.config import config
from scflows.custom_logger import logger
from scflows.tools import refresh_metadata

async def dprocess(device, dry_run = False):
    '''
        This function processes a device from SC API assuming there
        is postprocessing information in it and that it's valid for doing
        so
    '''
    task_log = []

    def logger_handler(msg, level='info'):
        if level == 'info':
            logger.info(msg)
        elif level == 'warning':
            logger.warning(msg)
        elif level == 'error':
            logger.error(msg)
        return f'{level}: {msg}'

    logger_handler(f'Processing instance for device {device}')

    # Create device from SC API
    # Changes made in flows (calibrations, blueprints) apply without restarting the worker
    refresh_metadata()

    d = sc.Device(params=sc.APIParams(id=device))
    task_state = [None, None]
    health = None

    if d:
        task_log.append(logger_handler(f'Device {device} Initialized'))

        if d.handler.postprocessing['latest_postprocessing'] is not None:
            d.options.min_date = d.handler.postprocessing['latest_postprocessing']
            task_log.append(logger_handler(f'Setting min_date as: {d.options.min_date }'))

        # Only load the sensors that the blueprint channels need
        d.options.channels = d.required_sensors
        task_log.append(logger_handler(f'Sensors to load: {d.options.channels}'))

        d.options.limit = config._max_load_amount

        if not d.options.channels:
            # An empty list would make the handler load all sensors
            task_log.append(logger_handler(f'Device {device} has no sensors needed by its blueprint', 'error'))
            task_state = ['ABORTED', 'NO_SENSORS_TO_LOAD']
        elif d.valid_for_processing:
            task_log.append(logger_handler('Device is valid for processing. Attempting load'))

            if await d.load():
                task_log.append(logger_handler(f'Device was loaded: {d.loaded}'))

                # Process it
                processed = d.process()
                # Checks of the blueprint (gaps, implausible, flat values, outliers), stored by flows
                health = health_checks(d, logger_handler, task_log)
                if processed:
                    task_log.append(logger_handler(f'Device was processed: {d.processed}'))

                    # Update postprocessing date
                    d.update_postprocessing_date()

                    # Post results
                    if d.postprocessing_updated:
                        if await d.post(columns = 'channels', dry_run=dry_run, max_retries=3, with_postprocessing=True):
                            task_log.append(logger_handler(f'Device {device} was posted'))
                            task_state = ['SUCCESS', 'PROCESSED AND UPLOADED']
                        else:
                            task_state = ['FAILED', 'DATA_POSTING_FAILED']
                    else:
                        task_log.append(logger_handler(f'Device {device} was not posted', 'warning'))
                        task_state = ['ABORTED', 'POSTPROCESSING_UPDATE_FAILED']
                else:
                    task_state = ['ABORTED', 'PROCESSING_FAILED']
            else:
                task_log.append(logger_handler(f'Device {device} was not loaded', 'warning'))

                if d.data.empty:
                    task_log.append(logger_handler(f'Device {device} data is empty. Nothing to do', 'warning'))
                    task_state = ['ABORTED', 'EMPTY_DATA']
        else:
            task_log.append(logger_handler(f'Device {device} not valid for processing', 'error'))
            task_state = ['ABORTED', 'NOT_VALID_FOR_PROCESSING']
    else:
        task_log.append(logger_handler(f'Device {device} not valid', 'error'))
        task_state = ['ABORTED', 'DEVICE_NOT_VALID']

    task_log.append(logger_handler(f'Concluded job for {device}'))

    return task_log, task_state, health


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

    log, state = asyncio.run(dprocess(args.device, dry_run=args.dry_run))
    logger.info(f'Result: {state}')
