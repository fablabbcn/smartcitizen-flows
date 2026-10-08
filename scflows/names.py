''' Keep the sensor names in sync with the Smart Citizen API and the SCK firmware

scdata renames the readings of each sensor id with these names, and the blueprints
use them. The API does not know them: its sensors have a long name and an old key
(e.g. id 55 is "Sensirion SHT31 - Temperature", key "t", name here TEMP). The firmware
does (lib/Sensors/Sensors.h, the short title of each sensor), but some names in flows
predate it and the blueprints use them, so names are never renamed automatically.

    flask --app scflows names sync

compares the names in flows with both and proposes, one at a time:
- a new name for each firmware sensor with an id but no name (e.g. a new sensor)
- the id of names that have none (0) when the firmware gives one
Everything else (different names for the same id, ids not in the API, ids with several
names, names without id that the firmware does not know) is reported for a person to
decide, with the blueprints that use each name. Units are never changed: scdata converts
readings with them, and the API units are not consistent ("C", "°C", "ºC").
'''
import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import urlparse

import click
import requests
from flask import current_app
from flask.cli import AppGroup

from . import db, editing
from .models import Blueprint, SensorName

names_cli = AppGroup('names', help='Sensor names (names/SCDevice.json)')

FIRMWARE_URL = 'https://raw.githubusercontent.com/fablabbcn/smartcitizen-kit-2x/master/lib/Sensors/Sensors.h'
# Who the changes are recorded for in the history
SYNC_USER = SimpleNamespace(id=None, username='names sync')

# OneSensor { location, priority, type, "SHORT_TITLE", "Title", platform id, enabled, every n, "unit", ... }
FIRMWARE_ROW = re.compile(r'OneSensor\s*\{\s*\w+\s*,\s*\d+\s*,\s*\w+\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*(\d+)'
                          r'\s*,\s*(?:true|false)\s*,\s*\d+\s*(?:,\s*"([^"]*)")?')


@dataclass(frozen=True)
class FirmwareSensor:
    name: str
    title: str
    id: int
    unit: str


@dataclass
class Change:
    name: str
    before: dict
    after: dict
    reasons: list = field(default_factory=list)

    @property
    def is_new(self):
        return self.before is None


def parse_firmware(source):
    ''' Sensors of the firmware (Sensors.h), without commented out rows '''
    source = re.sub(r'//[^\n]*', '', source)
    return [FirmwareSensor(name=short, title=title, id=int(sensor_id), unit=unit)
            for short, title, sensor_id, unit in FIRMWARE_ROW.findall(source)]


def read_firmware(location):
    ''' Sensors.h from a url or a local path '''
    if urlparse(location).scheme in ('http', 'https'):
        response = requests.get(location, timeout=30)
        response.raise_for_status()
        return parse_firmware(response.text)
    with open(location) as file:
        return parse_firmware(file.read())


def api_sensors(api_url=None):
    ''' {id: {name, description, unit}} of every sensor in the Smart Citizen API '''
    url = f"{api_url or current_app.config['SC_API_URL']}sensors?per_page=100"
    sensors = {}
    while url:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        for sensor in response.json():
            sensors[sensor['id']] = {key: sensor.get(key) or '' for key in ('name', 'description', 'unit')}
        url = response.links.get('next', {}).get('url')
    return sensors


def used_by_blueprints():
    ''' {name: [blueprints]} of the names that appear in the blueprints in flows '''
    names = list(db.session.execute(db.select(SensorName.name)).scalars())
    used = {}
    for blueprint in db.session.execute(db.select(Blueprint).order_by(Blueprint.name)).scalars():
        # Names appear as json strings: in channel inputs, kwargs and depends_on
        text = json.dumps(blueprint.body)
        for name in names:
            if f'"{name}"' in text:
                used.setdefault(name, []).append(blueprint.name)
    return used


def plan(names, api, firmware, used=None):
    '''
    Changes to propose and notices to report.
    names: [{name, id, description, unit}] in order. api: {id: {name, description, unit}}.
    firmware: [FirmwareSensor]. used: {name: [blueprints]}
    '''
    used = used or {}
    by_name = {item['name']: item for item in names}
    position = {item['name']: index for index, item in enumerate(names)}
    by_id = {}
    for item in names:
        by_id.setdefault(item['id'], []).append(item['name'])
    changes = {}
    notices = []

    def usage(name):
        return f' (used by {", ".join(used[name])})' if name in used else ''

    def change(name, before, after, reason):
        item = changes.setdefault(name, Change(name, before, dict(before or {}, name=name)))
        item.after.update(after)
        item.reasons.append(reason)

    for sensor in firmware:
        if not sensor.id:
            continue
        current = by_name.get(sensor.name)
        if current is not None:
            if current['id'] == 0:
                others = [name for name in by_id.get(sensor.id, []) if name != sensor.name]
                reason = f'the firmware gives {sensor.name} the id {sensor.id}'
                if others:
                    # scdata uses the first name of an id, in the order of the list
                    first = min(others + [sensor.name], key=position.get)
                    reason += (f'; {", ".join(others)} already {"has" if len(others) == 1 else "have"} it, '
                               f'scdata would use {first}')
                change(sensor.name, current, {'id': sensor.id}, reason)
            elif current['id'] != sensor.id:
                notices.append(f'{sensor.name}: id {current["id"]} in flows, {sensor.id} in the firmware')
        elif sensor.id not in by_id:
            # Units as the firmware writes them, like the existing names
            change(sensor.name, None, {'id': sensor.id, 'description': sensor.title, 'unit': sensor.unit},
                   f'new in the firmware: "{sensor.title}"'
                   + ('' if sensor.id in api else '; not in the Smart Citizen API yet'))
            # The firmware repeats some sensors (one row per board variant): propose them once
            by_name[sensor.name] = changes[sensor.name].after
            by_id[sensor.id] = [sensor.name]
        else:
            current_names = ', '.join(f'{name}{usage(name)}' for name in by_id[sensor.id])
            notices.append(f'id {sensor.id}: {current_names} in flows, {sensor.name} in the firmware')

    firmware_names = {sensor.name for sensor in firmware}
    without_id = []
    for item in names:
        sensor_id = changes[item['name']].after['id'] if item['name'] in changes else item['id']
        if not sensor_id:
            if item['name'] not in firmware_names:
                without_id.append(f'{item["name"]}{usage(item["name"])}')
        elif sensor_id not in api:
            notices.append(f'{item["name"]}{usage(item["name"])}: id {sensor_id} is not in the Smart Citizen API')
    if without_id:
        notices.append(f'{len(without_id)} names have no id and the firmware has no sensor with that name '
                       f'(renamed in the firmware, or only on the SD card): {", ".join(without_id)}')

    for sensor_id, ids_names in sorted(by_id.items()):
        if sensor_id and len(ids_names) > 1:
            listed = ', '.join(f'{name}{usage(name)}' for name in ids_names)
            notices.append(f'id {sensor_id} has several names: {listed}. scdata uses {ids_names[0]}')

    named = set(by_id) | {item.after['id'] for item in changes.values()}
    in_firmware = {sensor.id for sensor in firmware}
    unnamed = sorted(sensor_id for sensor_id in api if sensor_id not in named and sensor_id not in in_firmware)
    if unnamed:
        notices.append(f'{len(unnamed)} sensors of the Smart Citizen API have no name and are not in the firmware '
                       f'(older kits, or groups of sensors): {", ".join(map(str, unnamed))}')

    return list(changes.values()), notices


def describe(change):
    if change.is_new:
        after = change.after
        return f'Add {change.name}: id {after["id"]}, "{after["description"]}", unit "{after["unit"]}"'
    fields = [f'{key} {change.before[key]!r} -> {change.after[key]!r}'
              for key in ('id', 'description', 'unit') if change.before[key] != change.after[key]]
    return f'Update {change.name}: {", ".join(fields)}'


@names_cli.command('sync')
@click.option('--firmware', 'firmware_location', default=FIRMWARE_URL, show_default=True,
              help='Sensors.h of the SCK firmware: url or local path')
@click.option('--yes', is_flag=True, help='Apply every proposed change without asking')
@click.option('--dry-run', is_flag=True, help='Only show what would change')
def sync_command(firmware_location, yes, dry_run):
    ''' Compare the names with the Smart Citizen API and the firmware, and apply the accepted changes '''
    names = [item.to_json() for item in
             db.session.execute(db.select(SensorName).order_by(SensorName.position)).scalars()]
    api = api_sensors()
    firmware = read_firmware(firmware_location)
    click.echo(f'{len(names)} names in flows, {len(api)} sensors in the Smart Citizen API, '
               f'{len(firmware)} in the firmware')
    changes, notices = plan(names, api, firmware, used_by_blueprints())

    if notices:
        click.echo('\nTo review by hand (not changed):')
        for notice in notices:
            click.echo(f'  - {notice}')

    if not changes:
        click.echo('\nNothing to change')
        return
    click.echo(f'\n{len(changes)} proposed changes:')
    applied = 0
    apply_all = yes
    for change in changes:
        click.echo(f'\n{describe(change)}')
        for reason in change.reasons:
            click.echo(f'    {reason}')
        if dry_run:
            continue
        if not apply_all:
            answer = click.prompt('Apply? [y]es, [n]o, [a]ll remaining, [q]uit', default='n',
                                  type=click.Choice(['y', 'n', 'a', 'q']), show_choices=False)
            if answer == 'q':
                break
            if answer == 'n':
                continue
            apply_all = answer == 'a'
        editing.save_name(change.name, change.after, identity=SYNC_USER)
        applied += 1

    if dry_run:
        click.echo('\nDry run: nothing changed')
        return
    db.session.commit()
    click.echo(f'\n{applied} of {len(changes)} changes applied (history user: {SYNC_USER.username})')
