''' CSRF protection for the forms of the web interface: a token kept in the session '''
import hmac
import secrets

from flask import abort, request, session


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def check_csrf():
    ''' before_request for blueprints with forms '''
    if request.method != 'POST':
        return
    # Without a token in the session (no form was rendered), nothing matches
    expected, sent = session.get('csrf'), request.form.get('csrf')
    if not expected or not sent or not hmac.compare_digest(sent, expected):
        abort(400, 'The form expired, reload the page')


def protect(blueprint):
    blueprint.before_request(check_csrf)
    blueprint.app_context_processor(lambda: {'csrf_token': csrf_token})
