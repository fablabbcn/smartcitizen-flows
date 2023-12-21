from os.path import join
from os import makedirs
import sys

from scdata.utils import std_out
from smartcitizen_connector import search_by_query
from scdata import Device
import asyncio

from scheduler import Scheduler

async def check_device(device):
    try:
        d = Device(
            source={
                'type': 'api',
                'module': 'smartcitizen_connector',
                'handler': 'SCDevice'},
            params={'id': f'{device}'})
        valid = d.valid_for_processing
    except:
        std_out(f'Device {device} returned an error, ignoring', 'ERROR')
        pass
    else:
        return (device, valid)
    return (device, False)

async def dschedule(interval_hours, dry_run = False):
    '''
        This function schedules processing SC API devices based
        on the result of a global query for data processing
        in the SC API
    '''
    try:
        df = search_by_query(endpoint = 'devices',
            key="postprocessing_id", search_matcher="eq", value="not_null")
    except:
        pass
        return None

    # Check devices to postprocess first
    tasks = []

    for device in df.index:
        tasks.append(asyncio.ensure_future(check_device(device)))
    dl = await asyncio.gather(*tasks)

    for d in dl:
        if not d[1]: continue
        # Set scheduler
        s = Scheduler()
        # Define task
        task = f'{config._device_processor}.py --device {d[0]}'
        #Create log output if not existing
        dt = join(config.paths['tasks'], str(d[0]))
        makedirs(dt, exist_ok=True)
        log = f"{join(dt, f'{config._device_processor}_{d[0]}.log')}"
        # Schedule task
        s.schedule_task(task = task,
                        log = log,
                        interval = f'{interval_hours}H',
                        dry_run = dry_run,
                        load_balancing = True)

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv:
        print('dschedule: Schedule tasks for devices to process in SC API')
        print('USAGE:\n\rdschedule.py [options]')
        print('options:')
        print('--interval-hours: task execution interval in hours (default: config._postprocessing_interval_hours)')
        print('--dry-run: dry run')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    if '--interval-hours' in sys.argv:
        interval = int(sys.argv[sys.argv.index('--interval-hours')+1])
    else:
        interval = config._postprocessing_interval_hours

    loop = asyncio.get_event_loop()

    loop.run_until_complete(dschedule(interval, dry_run))
    loop.close()
