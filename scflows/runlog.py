''' Lines of the log of a run: "<UTC time> <level>: <message>", e.g. "2026-10-16 10:15:03 info: Loaded" '''
import re
from datetime import datetime, timezone

STAMP = '%Y-%m-%d %H:%M:%S'
STAMPED = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)', re.DOTALL)
LEVELS = ('info', 'warning', 'error', 'debug')


def log_line(level, message, created=None):
    ''' A log line, stamped now or at created (a timestamp, as in logging records) '''
    when = datetime.fromtimestamp(created, timezone.utc) if created is not None else datetime.now(timezone.utc)
    return f'{when:{STAMP}} {level}: {message}'


def unstamped(line):
    ''' The line without its time ("info: Loaded"): lines of runs before the stamps have none '''
    found = STAMPED.fullmatch(line)
    return found.group(2) if found else line


def parse(line):
    ''' (time or '', level or '', message) of a line '''
    found = STAMPED.fullmatch(line)
    stamp, rest = (found.group(1), found.group(2)) if found else ('', line)
    level, separator, message = rest.partition(': ')
    if not separator or level not in LEVELS:
        return stamp, '', rest
    return stamp, level, message


def tagged(line, tag):
    ''' The line with [tag] before its message, keeping its time and level (the backup lines of a long run) '''
    found = STAMPED.fullmatch(line)
    stamp, level, message = parse(line)
    rest = f'{level}: [{tag}] {message}' if level else f'[{tag}] {message}'
    return f'{stamp} {rest}' if found else rest
