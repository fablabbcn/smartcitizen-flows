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


DATA = os.path.join(os.path.dirname(__file__), 'data', 'smartcitizen-data')


@pytest.fixture
def app(tmp_path):
    ''' App with a migrated SQLite database '''
    from flask_migrate import upgrade

    from scflows import create_app

    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'db.sqlite'}"})
    with app.app_context():
        upgrade()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()
