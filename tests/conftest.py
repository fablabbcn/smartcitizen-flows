import os

# Read at import time by scflows.worker and scflows.create_app
os.environ.setdefault('CELERY_BROKER', 'memory://')
os.environ.setdefault('CELERY_RESULTS_BACKEND', 'cache+memory://')
os.environ.setdefault('CELERY_TIMEZONE', 'UTC')
os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite://')

import pytest
from crontab import CronTab


@pytest.fixture(autouse=True)
def no_user_crontab(monkeypatch):
    ''' Never write the crontab of the user running the tests '''
    monkeypatch.setattr(CronTab, 'write_to_user', lambda self, user=True: None)
