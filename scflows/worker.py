from celery import Celery
from os import environ

app = Celery('scflows',
    backend=environ['CELERY_RESULTS_BACKEND'],
    broker=environ['CELERY_BROKER'],
    include=[
        # 'scflows.tasks.dschedule',
        'scflows.tasks.dprocess',
        'scflows.tasks.dbackup'])

app.conf.timezone = environ['CELERY_TIMEZONE']

if __name__ == '__main__':
    app.start()