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

    result = []

    logger.info(f'Processing instance for device {device}')

    # Create device from SC API
    d = sc.Device(params=sc.APIParams(id=device))
    if d:
        result.append(f'Device {device} Initialized')
        logger.info(f'Device {device} Initialized')

    if d.handler.postprocessing['latest_postprocessing'] is not None:
        d.options.min_date = d.handler.postprocessing['latest_postprocessing']
        result.append(f'Setting min_date as: {d.options.min_date }')
        logger.info(f'Setting min_date as: {d.options.min_date }')

    if d.valid_for_processing:
        logger.info('Device is valid for processing. Attempting load')
        result.append('Device is valid for processing. Attempting load')

        # TODO Decide if this should be done for all, or just some
        # Maybe reduce to daily calculations
        # if await d.load(max_amount=config._max_load_amount):
        if await d.load():
            logger.info(f'Device was loaded: {d.loaded}')
            result.append(f'Device was loaded: {d.loaded}')
            # Process it
            d.process()
            logger.info(f'Device was processed: {d.processed}')
            result.append(f'Device was processed: {d.processed}')
            # TODO Add checks here?
            # Post results
            if await d.post(columns = 'metrics', dry_run=dry_run,
                max_retries=config._max_http_retries,
                with_postprocessing=True):
                logger.info(f'Device {device} was posted')
                result.append(f'Device was posted')
            else:
                logger.info(f'Device {device} was not posted')
                result.append(f'Device {device} was not posted')
        else:
            result.append(f'Device {device} was not loaded')
            logger.info(f'Device {device} was not loaded')
            if d.data.empty:
                result.append(f'Device {device} data is empty. Nothing to do')
                logger.info(f'Device {device} data is empty. Nothing to do')
    else:
        logger.error(f'Device {device} not valid')
        result.append(f'Device {device} not valid')
    logger.info(f'Concluded job for {device}')

    return result

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


