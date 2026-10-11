import logging
class Config(object):

    # Jobs: hours between runs
    _postprocessing_task_exec_interval_hours = 3
    _backup_task_exec_interval_hours = 6
    # Days of data per backup run
    _backup_interval_days = 20

    _log_level = logging.INFO
    _timestamp = True
    _avoid_negative_conc = True
    _max_load_amount = 1000
    _max_http_retries = 3

config = Config()
