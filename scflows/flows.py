import sys
from os.path import join, exists, abspath
from os import makedirs

from scflows.tasks.scheduler import Scheduler, Task
from scflows.config import config

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv:
        print('scflow: Process device of SC API')
        print('USAGE:\n\scflow.py [options] action')
        print('options:')
        print('--dry-run: dry run')
        print('--force-first-run: force first time running job')
        print('--overwrite: overwrite if it exists already')
        print('actions: auto-schedule, manual-schedule')
        print('---')
        print('auto-schedule --celery --interval-days <interval-days> (config._scheduler_interval_days):')
        print('\tschedule devices postproccesing check based on device postprocessing in platform')
        print('\tauto-schedule makes a global task for checking on interval-days interval and then the actual tasks are scheduled based on default intervals')
        print('manual-schedule --device <device> --interval-hours <interval-hours> (config._default_task_exec_interval_hours) [--task-file <file>.py] [--celery]:')
        print('\tschedule device processing manually')
        sys.exit()

    if '--force-first-run' in sys.argv: force_first_run = True
    else: force_first_run = False

    if '--overwrite' in sys.argv: overwrite = True
    else: overwrite = False

    if 'auto-schedule' in sys.argv:
        if '--interval-days' in sys.argv:
            interval = int(sys.argv[sys.argv.index('--interval-days')+1])
        else:
            interval = config._scheduler_interval_days

        script = f'{config._device_scheduler}.py'
        task_options = []

        if '--celery' in sys.argv: task_options.append('--celery')
        if '--dry-run' in sys.argv: task_options.append('--dry-run')

        t = Task(script = script, options=task_options)

        log = abspath(join(config.paths['log']))
        makedirs(log, exist_ok=True)

        # Setup scheduler
        s = Scheduler()
        s.schedule_task(task = t,
                        log = join(log, config._scheduler_log),
                        interval = f'{interval}D',
                        force_first_run = force_first_run,
                        overwrite = overwrite)

        sys.exit()

    if 'manual-schedule' in sys.argv:
        if '--device' not in sys.argv:
            print ('Cannot process without a devide ID')
            sys.exit()

        if '--interval-hours' in sys.argv:
            interval = int(sys.argv[sys.argv.index('--interval-hours')+1])
        else:
            interval = config._default_task_exec_interval_hours

        if '--task-file' in sys.argv:
            task = sys.argv[sys.argv.index('--task-file')+1]
            if not exists(task):
                print('Task file doesnt exist')
                sys.exit()
        else:
            script = f'{config._device_processor}.py'

        task_options = []

        if '--celery' in sys.argv: task_options.append('--celery')
        if '--dry-run' in sys.argv: task_options.append('--dry-run')


        device = int(sys.argv[sys.argv.index('--device')+1])
        task_options.append(f'--device {device}')

        t = Task(script = script, options=task_options)

        dt = join(config.paths['log'], str(device))
        makedirs(dt, exist_ok=True)

        # Setup scheduler
        s = Scheduler()
        s.schedule_task(task = t,
                        log = join(dt, f'{device}.log'),
                        interval = f'{interval}H',
                        force_first_run = force_first_run,
                        overwrite = overwrite)
        sys.exit()
