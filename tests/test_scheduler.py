import pytest
from crontab import CronTab

from scflows.cron import parsetabfiles, validate
from scflows.tasks.scheduler import Scheduler, Task


@pytest.fixture
def scheduler(tmp_path):
    tabfile = tmp_path / 'tabfile.tab'
    tabfile.touch()
    return Scheduler(tabfile=str(tabfile))


def process_task(device=13238):
    return Task(script='dprocess.py', options=[f'--device {device}', '--celery'])


def test_task():
    task = process_task()

    assert task.command == 'dprocess.py --device 13238 --celery'
    assert task.name == 'dprocess_device_13238_celery'
    assert task.instruction.endswith('scflows/tasks/dprocess.py --device 13238 --celery')


def test_schedule_task_every_hours(scheduler, tmp_path):
    scheduler.schedule_task(task=process_task(), log=str(tmp_path / 'log'), interval='3H', load_balancing=True)

    job = next(CronTab(tabfile=scheduler.tabfile).find_comment('dprocess_device_13238_celery'))
    hours = [int(hour) for hour in str(job.hour).split(',')]
    assert len(hours) == 8
    assert all(b - a == 3 for a, b in zip(hours, hours[1:]))
    assert job.minute.parts[0] in range(60)
    assert job.command.endswith(f">> {tmp_path / 'log'} 2>&1")


def test_schedule_task_every_days_and_minutes(scheduler, tmp_path):
    scheduler.schedule_task(task=Task('dschedule.py', ['--task process']), log='log', interval='1D')
    scheduler.schedule_task(task=Task('dbackup.py', ['--device 1']), log='log', interval='15M')

    tab = CronTab(tabfile=scheduler.tabfile)
    assert str(next(tab.find_comment('dschedule_task_process')).slices) == '@daily'
    assert str(next(tab.find_comment('dbackup_device_1')).minute) == '*/15'


def test_existing_task_is_kept_unless_overwritten(scheduler):
    task = process_task()
    scheduler.schedule_task(task=task, log='first', interval='3H')
    scheduler.schedule_task(task=task, log='second', interval='3H')

    assert scheduler.list_tasks() == [task.name]
    assert 'first' in next(scheduler.cron.find_comment(task.name)).command

    scheduler.schedule_task(task=task, log='second', interval='3H', overwrite=True)

    assert scheduler.list_tasks() == [task.name]
    assert 'second' in next(scheduler.cron.find_comment(task.name)).command


def test_remove_and_clear_tasks(scheduler):
    scheduler.schedule_task(task=process_task(1), log='log', interval='3H')
    scheduler.schedule_task(task=process_task(2), log='log', interval='3H')

    scheduler.remove_task(task_name='dprocess_device_1_celery')
    assert scheduler.list_tasks() == ['dprocess_device_2_celery']

    scheduler.clear_tasks()
    assert scheduler.list_tasks() == []
    assert CronTab(tabfile=scheduler.tabfile).crons == []


def test_parsetabfiles(scheduler, tmp_path):
    scheduler.schedule_task(task=process_task(), log=str(tmp_path / 'device.log'), interval='1D')

    tabfiles = parsetabfiles(path=str(tmp_path))

    job = tabfiles['tabfile']['dprocess_device_13238_celery']
    assert job['enabled'] is True
    assert job['valid'] is True
    assert job['task'].endswith('dprocess.py --device 13238 --celery')
    assert job['logfile'] == str(tmp_path / 'device.log')


def test_parsetabfiles_missing_path(tmp_path):
    assert parsetabfiles(path=str(tmp_path / 'missing')) == {}


def test_validate():
    assert validate('@daily', '/usr/bin/python', 'dprocess.py --device 1', 'log') is None
    assert validate('61 * * * *', '/usr/bin/python', 'dprocess.py --device 1', 'log') == 'Time slice error'
