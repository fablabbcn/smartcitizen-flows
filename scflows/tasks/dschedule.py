from os.path import join, abspath, dirname
from os import makedirs
import sys
import asyncio
from requests import get
from smartcitizen_connector import search_by_query
from smartcitizen_connector.device import check_postprocessing

from scflows.config import config
from scflows.tasks.scheduler import Scheduler, Task
from scflows.worker import app
from scflows.custom_logger import logger

async def check_and_schedule(device, interval_hours, task, dry_run, celery):
    _,_,status = check_postprocessing(device.postprocessing)

    if not status:
        logger.warning(f'Device {device._name} has no valid postprocessing')
        return

    # Define task
    task_options = [f'--device {device._name}']

    if celery: task_options.append('--celery')

    if task == 'process':
        script = f'{config._device_processor}.py'
        if dry_run: task_options.append('--dry-run')
    elif task == 'backup':
        script = f'{config._device_storer}.py'
    t = Task(script = script, options=task_options)

    # Set scheduler
    s = Scheduler()

    #Create log output if not existing
    dt = abspath(join(config.paths['public'], 'tasks', 'log', str(device._name)))
    makedirs(dt, exist_ok=True)
    log = f"{join(dt, f'{config._device_processor}_{device._name}.log')}"

    if device.last_reading_at is None:
        logger.warning(f'Device {device._name} has no readings yet')
        s.remove_task(t)
        return
    else:
        if device.postprocessing['latest_postprocessing'] is None:
            to_process = True
        else:
            if device.postprocessing['latest_postprocessing'] > device.last_reading_at:
                logger.warning(f'Device {device._name} has nothing to process. Remove')
                s.remove_task(t)
                return
            else:
                to_process = True

    if to_process:
        # Schedule task
        s.schedule_task(task = t,
                       log = log,
                       interval = f'{interval_hours}H',
                       load_balancing = True)
    return

async def dschedule(interval_hours, task = 'process', dry_run = False, celery = False):
    '''
        This function schedules processing SC API devices based
        on the result of a global query for data processing
        in the SC API
    '''
    df = search_by_query(endpoint='devices',
                         search_items=[{
                            'key': 'postprocessing_id',
                            'value': 'not_null',
                            'full': True
                        }])
    logger.info(df)

    # Check devices to postprocess first
    tasks = []

    for d in df.index:
        tasks.append(check_and_schedule(device=df.loc[d,: ],
            interval_hours=interval_hours,
            task = task,
            dry_run=dry_run,
            celery = celery))

    await asyncio.gather(*tasks)

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv:
        print('dschedule: Schedule tasks for devices to process in SC API')
        print('USAGE:\n\rdschedule.py [options]')
        print('options:')
        print('--interval-hours [interval]: task execution interval in hours (default: config._default_task_exec_interval_hours)')
        print('--task [type]: task type. Either \'process\' or \'backup\'')
        print('--celery: subtask execution is managed via celery worker')
        print('--dry-run: dry run')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    if '--task' in sys.argv:
        task = sys.argv[sys.argv.index('--task')+1]
    else:
        task = 'process'

    if '--interval-hours' in sys.argv:
        interval = int(sys.argv[sys.argv.index('--interval-hours')+1])
    else:
        if task == 'process':
            interval = config._postprocessing_task_exec_interval_hours
        elif task == 'backup':
            interval = config._backup_task_exec_interval_hours
        else:
            interval = config._default_task_exec_interval_hours

    if '--celery' in sys.argv:
        celery = True
    else:
        celery = False

    logger.info(f'Parsing task with following arguments: dry_run={dry_run}, interval-hours={interval}, celery={celery}')

    loop = asyncio.get_event_loop()
    loop.run_until_complete(dschedule(interval_hours=interval, task = task, dry_run=dry_run, celery=celery))
    loop.close()

