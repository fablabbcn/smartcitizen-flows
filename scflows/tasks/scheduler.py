from crontab import CronTab
from os.path import join, realpath, dirname, abspath
import sys
import subprocess
from numpy import zeros, random, where

from scflows.config import config
from scflows.custom_logger import logger

class Task(object):
    """Wrapper class for Tasks"""
    def __init__(self, script, options):
        self.dry_run = 'dry_run' in options
        self.script = script
        self.options = ' '.join(options)
        self.command = ' '.join([self.script, self.options])
        self.instruction = f'{dirname(realpath(__file__))}/{self.command}'
        self.name = f'{self.command}'\
            .replace('-','').replace(' ', '_').replace('.py','')

class Scheduler(object):
    """Wrapper class for CronTab Task Scheduling"""
    def __init__(self, tabfile = None):

        if tabfile is None:
            self.tabfile = join(config.paths['tabs'] ,f'{config._tabfile}.tab')
        else:
            self.tabfile = tabfile

        self.cron = CronTab(tabfile=self.tabfile)

    def check_slots(self, frequency = 'hourly'):
        # Check frequency
        if frequency == 'hourly':
            sn = 60
            fn = 8760
        elif frequency == 'daily':
            sn = 24
            fn = 365
        # Check for slots
        slots = zeros(sn)
        for job in self.cron:
            if job.frequency() == fn:
                for part in job.minutes.parts:
                    slots[part]+=1

        # Return a random slot
        return random.choice(where(slots == slots.min())[0])

    def list_tasks(self):
        tasks = []
        for task in self.cron.comments:
            logger.info(f'Found task: {task}')
            tasks.append(task)

        return tasks

    def remove_task(self, task=None, task_name=''):

        if task is None:
            _tn = task_name
        else:
            _tn = task.name

        logger.info(f'Removing: {_tn}')

        if not _tn:
            logger.error('No task provided')
            return

        if self.cron.remove_all(comment=_tn):
            logger.info(f'Removed')
            self.write()
        else:
            logger.error(f'Task not found: {_tn}')

    def clear_tasks(self):
        self.cron.remove_all()
        self.write()

    def check_existing_task(self, task):
        l = []
        tasks = self.cron.find_comment(task.name)
        for _task in tasks: l.append(_task)
        if l:
            logger.info(f'{task.name} already running')
            return True
        else:
            logger.info(f'{task.name} not found')
            return False

    def write(self):
        self.cron.write_to_user(user=True)
        self.cron.write(self.tabfile)

    def schedule_task(self, task, log, interval, force_first_run = False,\
        overwrite = False, load_balancing = False):
        logger.info(f'Setting up {task.name}...')

        if self.check_existing_task(task):
            logger.info('Task already exists')
            if not overwrite:
                logger.info('Skipping')
                return
            else:
                logger.info('Removing')
                self.remove_task(task)

        # Make command
        command = f"{sys.executable} {task.instruction} >> {log} 2>&1"

        # Set cronjob
        job = self.cron.new(command=command, comment=task.name)

        # Workaround for parsing interval
        if interval.endswith('D'):
            job.every(int(interval[:-1])).days()
            # If load balancing, add in low slot
            if load_balancing:
                job.hour.on(self.check_slots('daily'))

        elif interval.endswith('H'):
            jobs_on=[item for item in range(random.randint(0, int(interval[:-1])-1),24,int(interval[:-1]))]
            # job.every(int(interval[:-1])).hours()
            job.hour.on(jobs_on[0])

            if jobs_on[1:]:
                for job_on in jobs_on[1:]:
                    job.hour.also.on(job_on)

            # If load balancing, add in low slot
            if load_balancing:
                job.minute.on(self.check_slots('hourly'))

        elif interval.endswith('M'):
            job.every(int(interval[:-1])).minutes()
            # No load balance for minutes

        self.write()

        # Workaround for macos?
        # subprocess.call(['crontab', self.tabfile])

        if force_first_run:
            logger.info('Running task for first time. This could take a while')
            job.run()

        logger.info('Done')

if __name__ == '__main__':
    app.start()