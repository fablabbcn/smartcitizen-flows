from datetime import timedelta
from os import environ
from os.path import dirname, join

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()

def create_app(config=None):
    app = Flask(__name__, template_folder='templates')

    app.config['SECRET_KEY'] = environ['FLASK_SECRET_KEY']
    app.config['SQLALCHEMY_DATABASE_URI'] = environ['SQLALCHEMY_DATABASE_URI']
    app.config['EXPLAIN_TEMPLATE_LOADING']=True
    # Smart Citizen API, used to verify tokens. Same variable as smartcitizen-connector
    app.config['SC_API_URL'] = environ.get('API_URL', 'https://api.smartcitizen.me/v0/').rstrip('/') + '/'
    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
    # Public address used in links (e.g. https://flows.smartcitizen.me). Defaults to the request host
    app.config['PUBLIC_URL'] = environ.get('PUBLIC_URL', '').rstrip('/') or None
    if config is not None:
        app.config.update(config)
    # Keep the key order of the stored json
    app.json.sort_keys = False

    db.init_app(app)
    # Schema changes: flask db upgrade
    migrate.init_app(app, db, directory=join(dirname(__file__), 'migrations'))

    # Web interface sessions (login with Smart Citizen accounts, see auth.py)
    from .auth import SESSION_HOURS, load_user
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=SESSION_HOURS)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = bool(app.config['PUBLIC_URL'] and app.config['PUBLIC_URL'].startswith('https'))

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.user_loader(load_user)
    login_manager.init_app(app)

    # blueprint for auth routes in our app
    from .auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint)

    # blueprint for non-auth parts of app
    from .main import main as main_blueprint
    app.register_blueprint(main_blueprint)

    # public metadata api
    from .api import api as api_blueprint
    app.register_blueprint(api_blueprint)

    from .metadata import metadata_cli
    app.cli.add_command(metadata_cli)

    return app
