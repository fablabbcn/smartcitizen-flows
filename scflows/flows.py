import sys
from os.path import join, exists, abspath
from os import makedirs

from scflows.tasks.scheduler import Scheduler
from scflows.config import config

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv:
        print('scflow: Process device of SC API')
        print('USAGE:\n\scflow.py [options] action')
        print('options:')
        print('--dry-run: dry run')
        print('--force-first-run: force first time running job')
        print('--overwrite: overwrite if it exists already')
        print('actions: auto-schedule or device-schedule')
        print('auto-schedule --celery --interval-days <interval-days> (config._postprocessing_interval_hours):')
        print('\tschedule devices postproccesing check based on device postprocessing in platform')
        print('\tauto-schedule makes a global task for checking on interval-days interval and then the actual tasks are scheduled based on default intervals')
        print('manual-schedule --device <device> --interval-hours <interval-hours> (config._postprocessing_interval_hours) [--task-file <file>.py] [--celery]:')
        print('\tschedule device processing manually')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    if '--force-first-run' in sys.argv: force_first_run = True
    else: force_first_run = False

    if '--overwrite' in sys.argv: overwrite = True
    else: overwrite = False

    if 'auto-schedule' in sys.argv:
        if '--interval-days' in sys.argv:
            interval = int(sys.argv[sys.argv.index('--interval-days')+1])
        else:
            interval = config._scheduler_interval_days

        # Celery back-end
        if '--celery' in sys.argv:
            _celery = '--celery'
        else:
            _celery = ''

        s = Scheduler()
        log = abspath(join(config.paths['log']))
        makedirs(log, exist_ok=True)
        s.schedule_task(task = f'{config._device_scheduler}.py {_celery}',
                        log = join(log, config._scheduler_log),
                        interval = f'{interval}D',
                        force_first_run = force_first_run,
                        overwrite = overwrite,
                        dry_run = dry_run)
        sys.exit()

    if 'manual-schedule' in sys.argv:
        if '--device' not in sys.argv:
            print ('Cannot process without a devide ID')
            sys.exit()
        if '--interval-hours' in sys.argv:
            interval = int(sys.argv[sys.argv.index('--interval-hours')+1])
        else:
            interval = config._postprocessing_interval_hours
        if '--task-file' in sys.argv:
            task = sys.argv[sys.argv.index('--task-file')+1]
            if not exists(task):
                print('Task file doesnt exist')
                sys.exit()
        else:
            task = f'{config._device_processor}.py'

        # Celery back-end
        if '--celery' in sys.argv:
            _celery = '--celery'
        else:
            _celery = ''

        # Setup scheduler
        s = Scheduler()
        device = int(sys.argv[sys.argv.index('--device')+1])
        dt = join(config.paths['log'], str(device))
        makedirs(dt, exist_ok=True)

        s.schedule_task(task = f'{task} --device {device} {_celery}',
                        log = join(dt, f'{device}.log'),
                        interval = f'{interval}H',
                        force_first_run = force_first_run,
                        overwrite = overwrite,
                        dry_run = dry_run)
        sys.exit()
