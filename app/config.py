class Config(object):

    # Scheduler
    _scheduler_interval_days = 1
    _device_scheduler = 'dschedule'
    _scheduler_log = 'scheduler.log'
    # Tasks
    _postprocessing_interval_hours = 1
    _device_processor = 'dprocess'
    # Defaults
    _default_tabfile = 'tabfile.tab'

    paths = {
        'tasks': 'tasks'
    }

config = Config()
