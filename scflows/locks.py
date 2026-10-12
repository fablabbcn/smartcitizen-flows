''' Locks in Redis, so that a device task never runs twice at the same time '''
import secrets
from os import environ

# Longest expected run: the lock expires after it if a worker dies
LOCK_SECONDS = 3 * 3600

_client = None

# Deletes the key only if it still holds our token
RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def client():
    global _client
    if _client is None:
        import redis
        # Short timeouts: pages that show the sync status do not hang if Redis is down
        _client = redis.Redis.from_url(environ.get('REDIS_URL', 'redis://redis:6379/1'),
                                       socket_connect_timeout=2, socket_timeout=5)
    return _client


def acquire(key, seconds=LOCK_SECONDS):
    ''' Returns a token if the lock was free, None otherwise '''
    token = secrets.token_hex(16)
    if client().set(key, token, nx=True, ex=seconds):
        return token
    return None


def release(key, token):
    client().eval(RELEASE, 1, key, token)
