class Config(object):

    # Scheduler
    _scheduler_interval_days = 1
    _device_scheduler = 'dschedule'
    _scheduler_log = 'scheduler.log'
    # Tasks
    _postprocessing_interval_hours = 1
    _device_processor = 'dprocess'

    paths = {
        'tasks': 'tasks',
        'public': 'public'
    }

    _tabfile = 'tabfile'
    _out_level = 'DEBUG'
    _timestamp = True
    _avoid_negative_conc = True
    _max_load_amount = 500
    _max_http_retries = 3

config = Config()
