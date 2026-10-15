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


def posting_enabled():
    ''' A Smart Citizen API token to post with (the API rejects an empty one) '''
    import os
    return bool(os.environ.get('SC_BEARER', '').strip())
