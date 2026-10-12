''' Web interface login with Smart Citizen accounts

Admins and researchers can sign in. The session keeps their identity (not the
Smart Citizen token) for SESSION_HOURS: researchers see the hardware their devices
had when they signed in.
'''
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import UserMixin, current_user, login_required, login_user, logout_user

from .identity import ADMIN, EDITORS, Identity, sign_in

auth = Blueprint('auth', __name__)

SESSION_HOURS = 12
SESSION_KEY = 'identity'


class SessionUser(UserMixin):
    def __init__(self, identity):
        self.identity = identity

    def get_id(self):
        return str(self.identity.id)

    @property
    def username(self):
        return self.identity.username

    @property
    def role(self):
        return self.identity.role

    @property
    def is_admin(self):
        return self.identity.role == ADMIN


def load_user(user_id):
    ''' Flask-Login user loader: the identity is kept in the session '''
    data = session.get(SESSION_KEY)
    if not data or str(data.get('id')) != user_id:
        return None
    return SessionUser(Identity(**data))


def requires_ui_role(*roles):
    ''' Requires a signed in user with one of the roles '''
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


admin_required = requires_ui_role(ADMIN)


@auth.route('/login')
def login():
    return render_template('login.html')


@auth.route('/login', methods=['POST'])
def login_post():
    identity = sign_in(request.form.get('name', ''), request.form.get('password', ''))
    if identity is None:
        flash('Please check your login details and try again.', 'error')
        return redirect(url_for('auth.login'))
    if identity.role not in EDITORS:
        flash('Only Smart Citizen admins and researchers can sign in.', 'error')
        return redirect(url_for('auth.login'))

    session[SESSION_KEY] = {'id': identity.id, 'username': identity.username, 'role': identity.role,
                            'hardware': list(identity.hardware), 'devices': list(identity.devices)}
    session.permanent = True
    login_user(SessionUser(identity))
    if identity.role == ADMIN:
        return redirect(url_for('jobs_ui.index'))
    return redirect(url_for('main.index'))


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop(SESSION_KEY, None)
    return redirect(url_for('main.index'))
