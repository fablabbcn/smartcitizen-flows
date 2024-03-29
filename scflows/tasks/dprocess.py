# internal imports
from scdata import Device
from scdata.utils import std_out

import sys
import asyncio
from scflows.worker import app
from scflows.config import config

async def dprocess(device, dry_run = False):
    '''
        This function processes a device from SC API assuming there
        is postprocessing information in it and that it's valid for doing
        so
    '''
    std_out(f'[SCFLOW] Processing instance for device {device}')
    # Create device from SC API
    d = Device(source={'type': 'api',
                        'module': 'smartcitizen_connector',
                        'handler': 'SCDevice'},
                params={'id': f'{device}'})
    if d.valid_for_processing:
        # Load only unprocessed
        d.min_date = d.handler.postprocessing.latest_postprocessing
        d.resample = False
        d.frequency = '1Min'
        if await d.load(max_amount=config._max_load_amount):
            std_out(f'[SCFLOW] Device {device} loaded')
            # Process it
            d.process()
            # Post results
            if await d.post(columns = 'metrics', dry_run=dry_run,
                max_retries=config._max_http_retries,
                with_postprocessing=True):
                std_out(f'[SCFLOW] Device {device} posted')
    else:
        std_out(f'[SCFLOW] Device {device} not valid', 'ERROR')
    std_out(f'[SCFLOW] Concluded job for {device}')

@app.task(track_started=True, name='scflows.tasks.dprocess_task')
def dprocess_task(device, dry_run=False):
    asyncio.run(dprocess(device, dry_run))

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
        std_out('Missing device', 'ERROR')
        sys.exit()

    if '--celery' in sys.argv:
        dprocess_task.delay(device, dry_run)
    else:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(dprocess(device, dry_run))
        loop.close()


