import logging
class Config(object):

    # Scheduler
    _scheduler_interval_days = 1
    _device_scheduler = 'dschedule'
    _scheduler_log = 'scheduler.log'

    # Tasks
    _default_task_exec_interval_hours = 24

    _device_processor = 'dprocess'
    _postprocessing_task_exec_interval_hours = 3

    _device_storer = 'dbackup'
    _backup_task_exec_interval_hours = 6
    _backup_interval_days = 20

    paths = {
        'tasks': 'tasks',
        'public': 'public',
        'tabs': 'public/tasks',
        'log': 'public/tasks/log'
    }

    _tabfile = 'tabfile'
    _log_level = logging.INFO
    _timestamp = True
    _avoid_negative_conc = True
    _max_load_amount = 500
    _max_http_retries = 3

config = Config()
