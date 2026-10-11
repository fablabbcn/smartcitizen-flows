''' Celery workers and beat schedule

Workers: celery --app scflows.worker:app worker
Beat (one instance): celery --app scflows.worker:app beat
'''
from os import environ

from celery import Celery, Task
from celery.schedules import crontab

_flask_app = None


def flask_app():
    ''' Tasks use the database through the Flask application, created on first use '''
    global _flask_app
    if _flask_app is None:
        from scflows import create_app
        _flask_app = create_app()
    return _flask_app


class FlaskTask(Task):
    def __call__(self, *args, **kwargs):
        with flask_app().app_context():
            return super().__call__(*args, **kwargs)


app = Celery('scflows', broker=environ['CELERY_BROKER'], backend=environ.get('CELERY_RESULTS_BACKEND') or None,
             task_cls=FlaskTask)

app.conf.update(
    timezone=environ.get('CELERY_TIMEZONE', 'UTC'),
    # Results are kept in the job_run table
    task_ignore_result=True,
    # A run is acknowledged when it finishes: it is not lost if a worker dies
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Redis redelivers unacknowledged tasks after this time: longer than the longest run (see locks)
    broker_transport_options={'visibility_timeout': 3 * 3600},
    beat_schedule={
        'dispatch-due-jobs': {'task': 'scflows.dispatch_due_jobs', 'schedule': 60.0},
        # Every hour: devices whose postprocessing was just set get their jobs within the hour
        'sync-jobs': {'task': 'scflows.sync_jobs', 'schedule': crontab(minute=0)},
        'prune-health': {'task': 'scflows.prune_health', 'schedule': crontab(hour=3, minute=30)},
    },
)


@app.task(name='scflows.run_job')
def run_job(run_id):
    from scflows.jobs import execute_run
    execute_run(run_id)


@app.task(name='scflows.dispatch_due_jobs')
def dispatch_due_jobs():
    from scflows.jobs import dispatch_due_jobs
    dispatch_due_jobs()


@app.task(name='scflows.sync_jobs')
def sync_jobs(source='schedule'):
    from scflows.jobs import run_sync
    run_sync(source=source)


@app.task(name='scflows.prune_health')
def prune_health():
    from scflows.health import prune
    prune()


if __name__ == '__main__':
    app.start()
