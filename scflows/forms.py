''' CSRF protection for the forms of the web interface: a token kept in the session '''
import secrets

from flask import abort, request, session


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def check_csrf():
    ''' before_request for blueprints with forms '''
    if request.method == 'POST' and request.form.get('csrf') != session.get('csrf'):
        abort(400, 'The form expired, reload the page')


def protect(blueprint):
    blueprint.before_request(check_csrf)
    blueprint.app_context_processor(lambda: {'csrf_token': csrf_token})
