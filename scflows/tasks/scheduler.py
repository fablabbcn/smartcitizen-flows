from crontab import CronTab
from os.path import join, realpath, dirname, abspath
import sys
import subprocess
from numpy import zeros, random, where

from scflows.config import config
from scflows.custom_logger import logger

class Scheduler(object):
    """Wrapper class for CronTab Task Scheduling"""
    def __init__(self, tabfile = None):
        self.cron = CronTab(user=True)
        if tabfile is None:
            self.tabfile = join(config.paths['tabs'] ,f'{config._tabfile}.tab')
        else:
            self.tabfile = tabfile

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

    def schedule_task(self, task, log, interval, force_first_run = False,\
        overwrite = False, dry_run = False, load_balancing = False):
        logger.info(f'Setting up {task}...')

        # Find if the task is already there
        comment = task.replace('--','').replace(' ', '_').replace('.py','')

        if self.check_existing(comment):
            logger.info('Task already exists')
            if not overwrite:
                logger.info('Skipping')
                return
            else:
                logger.info('Removing')
                self.remove(comment)

        # Check if dry_run
        if dry_run: _dry_run = '--dry-run'
        else: _dry_run = ''

        # Make command
        instruction = f'{dirname(realpath(__file__))}/{task} {_dry_run}'
        command = f"{sys.executable} {instruction} >> {log} 2>&1"
        print (command)

        # Set cronjob
        job = self.cron.new(command=command, comment=comment)

        # Workaround for parsing interval
        if interval.endswith('D'):
            job.every(int(interval[:-1])).days()
            # If load balancing, add in low slot
            if load_balancing: job.hour.on(self.check_slots('daily'))
        elif interval.endswith('H'):
            jobs_on=[item for item in range(random.randint(0, int(interval[:-1])-1),24,int(interval[:-1]))]
            # job.every(int(interval[:-1])).hours()
            job.hour.on(jobs_on[0])
            if jobs_on[1:]:
                for job_on in jobs_on[1:]: job.hour.also.on(job_on)
            # If load balancing, add in low slot
            if load_balancing: job.minute.on(self.check_slots('hourly'))
        elif interval.endswith('M'):
            job.every(int(interval[:-1])).minutes()
            # No load balance for minutes
        self.cron.write(self.tabfile)

        # Workaround for macos?
        subprocess.call(['crontab', self.tabfile])

        if force_first_run:
            logger.info('Running task for first time. This could take a while')
            job.run()

        logger.info('Done')

    def remove(self, comment):
        l = []
        c = self.cron.find_comment(comment)
        for item in c: self.cron.remove(item)
        self.cron.write(self.tabfile)

    def check_existing(self, comment):
        l = []
        c = self.cron.find_comment(comment)
        for item in c: l.append(c)
        if l:
            logger.info(f'{comment} already running')
            return True
        else:
            logger.info(f'{comment} not running')
            return False

if __name__ == '__main__':
    app.start()