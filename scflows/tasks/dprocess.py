import scdata as sc

import sys
import asyncio
from scflows.worker import app
from scflows.config import config
from scflows.custom_logger import logger
from celery.result import AsyncResult

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
    d = sc.Device(params=sc.APIParams(id=device))

    if d:
        task_log.append(logger_handler(f'Device {device} Initialized'))

        if d.handler.postprocessing['latest_postprocessing'] is not None:
            d.options.min_date = d.handler.postprocessing['latest_postprocessing']
            task_log.append(logger_handler(f'Setting min_date as: {d.options.min_date }'))

        # Only load data that is used in the metrics
        d.options.channels = sorted([item for item in set([metric.kwargs['channel'] for metric in d.metrics if 'channel' in metric.kwargs]) if item is not None])
        d.options.limit = config._max_load_amount

        if d.valid_for_processing:
            task_log.append(logger_handler('Device is valid for processing. Attempting load'))

            if await d.load():
                task_log.append(logger_handler(f'Device was loaded: {d.loaded}'))

                # Process it
                d.process()
                task_log.append(logger_handler(f'Device was processed: {d.processed}'))

                # Update postprocessing date
                d.update_postprocessing_date()

                # Post results
                if d.postprocessing_updated:
                    if await d.post(columns = 'metrics', dry_run=dry_run, max_retries=3, with_postprocessing=True):
                        task_log.append(logger_handler(f'Device {device} was posted'))
                else:
                    task_log.append(logger_handler(f'Device {device} was not posted', 'warning'))
            else:
                task_log.append(logger_handler(f'Device {device} was not loaded', 'warning'))

                if d.data.empty:
                    task_log.append(logger_handler(f'Device {device} data is empty. Nothing to do', 'warning'))
        else:
            task_log.append(logger_handler(f'Device {device} not valid for processing', 'error'))
    else:
        task_log.append(logger_handler(f'Device {device} not valid', 'error'))

    task_log.append(logger_handler(f'Concluded job for {device}'))

    return task_log

@app.task(track_started=True, name='scflows.tasks.dprocess_task')
def dprocess_task(device, dry_run=False):
    result = asyncio.run(dprocess(device, dry_run))
    logger.info('dprocess')
    logger.info(result)
    return result

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv:
        print('dprocess: Process device of SC API')
        print('USAGE:\n\rdprocess.py --device <device-number> [options]')
        print('options:')
        print('--device <device-number>: device to process')
        print('--celery: task execution is managed via celery worker')
        print('--dry-run: dry run')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    loop = asyncio.get_event_loop()

    if '--device' in sys.argv:
        device = int(sys.argv[sys.argv.index('--device')+1])
    else:
        logger.error('Missing device')
        sys.exit()

    logger.info(f'Processing device: {device}')

    if '--celery' in sys.argv:
        logger.info(f'Using celery backend...')
        task_id = dprocess_task.delay(device, dry_run)
        logger.info(f'Task ID: {task_id}')

        # Wait for result
        result = AsyncResult(task_id, app=app)
        result.wait(timeout=60)

        logger.info('Task result:')
        for res in result.get():
            logger.info(res)
    else:
        loop.run_until_complete(dprocess(device, dry_run))

    loop.close()


