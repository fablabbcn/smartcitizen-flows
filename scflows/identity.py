''' Identity of API callers, from Smart Citizen API tokens

Clients send "Authorization: Bearer <token>", the same token they use with the
Smart Citizen API. The token is checked against {API_URL}me and the result is
cached (see TOKEN_TTL).
'''
import hashlib
import threading
import time
from dataclasses import dataclass
from functools import wraps

import requests
from flask import abort, current_app, request

ADMIN = 'admin'
RESEARCHER = 'researcher'
EDITORS = (ADMIN, RESEARCHER)

# {API_URL}me embeds all the devices visible to the user: for admins it can take
# more than 30 seconds. Verified tokens are kept for an hour, invalid ones for a minute
ME_TIMEOUT = 60
TOKEN_TTL = 3600
INVALID_TOKEN_TTL = 60
# Expired tokens are dropped when the cache grows past this size
CACHE_SWEEP_SIZE = 1000

REQUEST_KEY = 'scflows.identity'


@dataclass(frozen=True)
class Identity:
    id: int
    username: str
    role: str


class TokenCache:
    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def get(self, token):
        key = self.key(token)
        with self._lock:
            item = self._items.get(key)
            if item is not None and item[1] < time.monotonic():
                del self._items[key]
                item = None
        return item

    def set(self, token, identity, ttl):
        now = time.monotonic()
        with self._lock:
            # Random tokens would otherwise grow the cache forever
            if len(self._items) >= CACHE_SWEEP_SIZE:
                self._items = {key: item for key, item in self._items.items() if item[1] >= now}
            self._items[self.key(token)] = (identity, now + ttl)

    def __len__(self):
        with self._lock:
            return len(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()


cache = TokenCache()


def verify_token(token):
    ''' Returns the Identity of a token, or None if the Smart Citizen API rejects it '''
    cached = cache.get(token)
    if cached is not None:
        return cached[0]

    try:
        response = requests.get(f"{current_app.config['SC_API_URL']}me",
                                headers={'Authorization': f'Bearer {token}'}, timeout=ME_TIMEOUT)
    except requests.Timeout:
        abort(503, f'Cannot verify the token: the Smart Citizen API did not answer in {ME_TIMEOUT} seconds')
    except requests.RequestException:
        abort(503, 'Cannot verify the token: the Smart Citizen API is not reachable')

    if response.status_code in (401, 403):
        cache.set(token, None, INVALID_TOKEN_TTL)
        return None
    if response.status_code != 200:
        abort(503, f'Cannot verify the token: the Smart Citizen API answered {response.status_code}')

    try:
        user = response.json()
        identity = Identity(id=user['id'], username=user['username'], role=user.get('role', 'citizen'))
    except (ValueError, TypeError, KeyError, AttributeError):
        # json.JSONDecodeError is a ValueError
        abort(503, 'Cannot verify the token: the Smart Citizen API answered an unexpected body')
    cache.set(token, identity, TOKEN_TTL)
    return identity


def current_identity():
    ''' Identity of the request, or None if it has no token '''
    # Stored on the request: an application context can outlive a request
    if REQUEST_KEY not in request.environ:
        header = request.headers.get('Authorization', '')
        scheme, _, token = header.partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            request.environ[REQUEST_KEY] = None
        else:
            identity = verify_token(token.strip())
            if identity is None:
                abort(401, 'Invalid token')
            request.environ[REQUEST_KEY] = identity
    return request.environ[REQUEST_KEY]


def requires_role(*roles):
    ''' Requires a token of a user with one of the roles '''
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            identity = current_identity()
            if identity is None:
                abort(401, 'Authorization required: send a Smart Citizen API token as "Authorization: Bearer <token>"')
            if identity.role not in roles:
                abort(403, f'Requires one of the roles: {", ".join(roles)}')
            return view(*args, **kwargs)
        return wrapper
    return decorator


def sign_in(username, password):
    '''
    Signs in with Smart Citizen credentials (POST {API_URL}sessions).
    Returns the Identity, or None if the credentials are wrong
    '''
    try:
        response = requests.post(f"{current_app.config['SC_API_URL']}sessions",
                                 json={'username': username, 'password': password}, timeout=30)
    except requests.RequestException:
        abort(503, 'Cannot sign in: the Smart Citizen API is not reachable')

    if response.status_code != 200:
        if response.status_code >= 500:
            abort(503, f'Cannot sign in: the Smart Citizen API answered {response.status_code}')
        return None

    return verify_token(response.json()['access_token'])
