from os import environ, name
from os.path import expanduser, join, isdir
from termcolor import colored
from datetime import datetime
from scflows.config import config
from scflows.custom_logger import logger
import re
import sys
import json

def parse_sc_json(in_payload):
    safe_payload = ''
    # Parse payload from sc into a dict
    for item in in_payload.strip("{").strip("}").split(','):
        r = item.split(":")
        a = f'\"{r[0]}\":\"{":".join(r[1:])}\"'
        safe_payload = ",".join([safe_payload, a])
    safe_payload = "{" + ",".join(safe_payload.split(',')[1:]) + "}"

    return json.loads(safe_payload)

class LazyCallable(object):
    '''
        Adapted from Alex Martelli's answer on this post on stackoverflow:
        https://stackoverflow.com/questions/3349157/python-passing-a-function-name-as-an-argument-in-a-function
    '''
    def __init__(self, name):
        self.n = name
        self.f = None
    def __call__(self, *a, **k):
        if self.f is None:
            logger.info(f"Loading {self.n.rsplit('.', 1)[1]} from {self.n.rsplit('.', 1)[0]}")
            modn, funcn = self.n.rsplit('.', 1)
            if modn not in sys.modules:
                __import__(modn)
            self.f = getattr(sys.modules[modn], funcn)
        return self.f(*a, **k)

def find_by_field(models, value, field):
    try:
        item = next(model for _, model in enumerate(models) if model.__getattribute__(field) == value)
    except StopIteration:
        logger.info(f'Column {field} not in models')
        pass
    else:
        return item
    return None

def url_checker(string):
    if string is not None:
        regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
        url = re.findall(regex,string)
        return [x[0] for x in url]
    else:
        return []

def load_env(env_file):
    print (env_file)
    try:
        with open(env_file) as f:
            for line in f:
                # Ignore empty lines or lines that start with #
                if line.startswith('#') or not line.strip(): continue
                # Load to local environ
                key, value = line.strip().split('=', 1)
                environ[key] = value

    except FileNotFoundError:
        print('.env file not found')
        return False
    else:
        return True

def get_tabfile_dir():

    # Check if windows
    _mswin = name == "nt"
    # Get user_home
    _user_home = expanduser("~")

    # Get .cache dir - maybe change it if found in config.json
    if _mswin:
        _ddir = environ["APPDATA"]
    elif 'XDG_CACHE_HOME' in environ:
        _ddir = environ['XDG_CACHE_HOME']
    else:
        _ddir = join(expanduser("~"), '.cache')

    dpath = join(_ddir, 'scdata', 'tasks')

    return dpath

def check_path(path):
    return isdir(path)