from os import environ
from termcolor import colored
from config import config
from datetime import datetime
import re

def url_checker(string):
    if string is not None:
        regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
        url = re.findall(regex,string)
        return [x[0] for x in url]
    else:
        return []

def load_env(env_file):
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

def std_out(msg, mtype = None):
    out_level = config._out_level
    if config._timestamp == True:
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    else:
        stamp = ''
    # Output levels:
    # 'QUIET': nothing,
    # 'NORMAL': warn, err
    # 'DEBUG': info, warn, err, success
    if out_level == 'QUIET': priority = 0
    elif out_level == 'NORMAL': priority = 1
    elif out_level == 'DEBUG': priority = 2


    if mtype is None and priority>1:
        print(f'[{stamp}] - ' + '[INFO] ' + msg)
    elif mtype == 'SUCCESS' and priority>0:
        print(f'[{stamp}] - ' + colored('[SUCCESS] ', 'green') + msg)
    elif mtype == 'WARNING' and priority>0:
        print(f'[{stamp}] - ' + colored('[WARNING] ', 'yellow') + msg)
    elif mtype == 'ERROR' and priority>0:
        print(f'[{stamp}] - ' + colored('[ERROR] ', 'red') + msg)