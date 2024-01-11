from os.path import join, abspath, dirname
from os import makedirs
import sys
import asyncio
from requests import get
from smartcitizen_connector import search_by_query, check_postprocessing

from scflows.tools import std_out
from scflows.config import config
from scflows.tasks.scheduler import Scheduler
from scflows.worker import app

async def check_and_schedule(id, postprocessing, interval_hours, dry_run, celery):
    _,_,status = check_postprocessing(postprocessing)
    if not status: return
    # Set scheduler
    s = Scheduler()
    # Define task
    task = f"{config._device_processor}.py --device {id}"
    #Create log output if not existing
    dt = abspath(join(config.paths['public'], 'tasks', 'log', str(id)))
    makedirs(dt, exist_ok=True)
    log = f"{join(dt, f'{config._device_processor}_{id}.log')}"
    # Schedule task
    s.schedule_task(task = task,
                    log = log,
                    interval = f'{interval_hours}H',
                    dry_run = dry_run,
                    load_balancing = True,
                    celery = celery)

async def dschedule(interval_hours, dry_run = False, celery = False):
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

    for d in df.index:
        tasks.append(check_and_schedule(id=d,
            postprocessing=df.loc[d, 'postprocessing'],
            interval_hours=interval_hours,
            dry_run=dry_run,
            celery = celery))

    await asyncio.gather(*tasks)

@app.task(track_started=True, name='scflows.tasks.dschedule_task')
def dschedule_task(interval_hours, dry_run = False):
    asyncio.run(dschedule(interval_hours, dry_run, celery = True))

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv:
        print('dschedule: Schedule tasks for devices to process in SC API')
        print('USAGE:\n\rdschedule.py [options]')
        print('options:')
        print('--interval-hours: task execution interval in hours (default: config._postprocessing_interval_hours)')
        print('--celery: task execution is managed via celery worker')
        print('--dry-run: dry run')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    if '--interval-hours' in sys.argv:
        interval = int(sys.argv[sys.argv.index('--interval-hours')+1])
    else:
        interval = config._postprocessing_interval_hours

    if '--celery' in sys.argv:
        dschedule_task.delay(interval, dry_run)
    else:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(dschedule(interval, dry_run))
        loop.close()

