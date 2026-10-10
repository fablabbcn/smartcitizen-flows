''' Import processing metadata from a smartcitizen-data checkout, and verify what is served '''
import json
from dataclasses import dataclass, field
from os.path import basename, join, splitext
from glob import glob
from urllib.parse import urlparse

import click
import requests
from flask import current_app
from flask.cli import AppGroup
from pydantic import ValidationError

from . import db, editing
from .models import Blueprint, Hardware, Revision

metadata_cli = AppGroup('metadata', help='Processing metadata (blueprints, hardware, calibrations)')


@dataclass
class ImportReport:
    created: dict = field(default_factory=lambda: {'blueprints': 0, 'hardware': 0, 'calibrations': 0})
    updated: dict = field(default_factory=lambda: {'blueprints': 0, 'hardware': 0, 'calibrations': 0})
    skipped: dict = field(default_factory=lambda: {'blueprints': 0, 'hardware': 0, 'calibrations': 0})
    errors: list = field(default_factory=list)


def load_json(path):
    with open(path) as file:
        return json.load(file)


def name_of(path):
    return splitext(basename(urlparse(str(path)).path))[0]


def load_or_report(path, report):
    ''' The json of a file, or None if it cannot be read (the error goes to the report) '''
    try:
        return load_json(path)
    except (OSError, ValueError) as error:
        # json.JSONDecodeError is a ValueError
        report.errors.append(f'{path}: cannot be read as JSON ({error})')
        return None


def import_metadata(path, overwrite=False):
    '''
    Imports blueprints, hardware and calibrations from a smartcitizen-data checkout.
    Existing items are kept unless overwrite is set. Invalid items are reported and skipped.
    '''
    report = ImportReport()

    def run(kind, counter, key, save, data, source):
        if not overwrite and editing.find(kind, key) is not None:
            report.skipped[counter] += 1
            return
        try:
            _, created = save(key, data, action=Revision.IMPORT)
        except (ValidationError, ValueError) as error:
            report.errors.append(f'{source}: {error}')
            return
        (report.created if created else report.updated)[counter] += 1

    for blueprint_path in sorted(glob(join(path, 'blueprints', '*.json'))):
        body = load_or_report(blueprint_path, report)
        if body is not None:
            run('blueprint', 'blueprints', name_of(blueprint_path), editing.save_blueprint, body, blueprint_path)
    db.session.flush()

    for hardware_path in sorted(glob(join(path, 'hardware', '*.json'))):
        body = load_or_report(hardware_path, report)
        if body is not None:
            run('hardware', 'hardware', name_of(hardware_path), editing.save_hardware, body, hardware_path)

    calibrations_path = join(path, 'calibrations', 'calibrations.json')
    for sensor_id, data in (load_or_report(calibrations_path, report) or {}).items():
        if not isinstance(data, dict):
            report.errors.append(f'{calibrations_path}: {sensor_id} is not an object')
            continue
        run('calibration', 'calibrations', sensor_id, editing.save_calibration, data,
            f'{calibrations_path} ({sensor_id})')

    db.session.commit()
    return report


def read_source(source, relative_path):
    ''' Reads a json file from a local checkout or a base url (e.g. raw.githubusercontent.com) '''
    if urlparse(source).scheme in ['http', 'https']:
        response = requests.get(f"{source.rstrip('/')}/{relative_path}", timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    try:
        return load_json(join(source, relative_path))
    except FileNotFoundError:
        return None


def verify_metadata(source):
    ''' Compares every item served by the API with the same file in source. Returns the differences '''
    client = current_app.test_client()
    differences = []

    def compare(served_path, relative_path, normalise=lambda data: data):
        served = client.get(f'/api/v1/{served_path}').get_json()
        expected = read_source(source, relative_path)
        if expected is None:
            differences.append(f'{relative_path}: not in source')
        elif normalise(served) != normalise(expected):
            differences.append(f'{relative_path}: served content differs')

    def hardware_by_blueprint_name(data):
        ''' Hardware served by flows links blueprints in flows: compare the blueprint names '''
        data = dict(data)
        url = data.pop('blueprint_url', None)
        data['blueprint'] = data.get('blueprint') or (name_of(url) if url else None)
        return data

    for blueprint in db.session.execute(db.select(Blueprint)).scalars():
        compare(f'blueprints/{blueprint.name}.json', f'blueprints/{blueprint.name}.json')
    for hardware in db.session.execute(db.select(Hardware)).scalars():
        compare(f'hardware/{hardware.name}.json', f'hardware/{hardware.name}.json', hardware_by_blueprint_name)
    compare('calibrations/calibrations.json', 'calibrations/calibrations.json')

    # Files of a local checkout that are not served
    if urlparse(source).scheme not in ['http', 'https']:
        for model, folder in [(Blueprint, 'blueprints'), (Hardware, 'hardware')]:
            served = set(db.session.execute(db.select(model.name)).scalars())
            for path in sorted(glob(join(source, folder, '*.json'))):
                if name_of(path) not in served:
                    differences.append(f'{folder}/{basename(path)}: not served')

    return differences


@metadata_cli.command('import')
@click.argument('path', type=click.Path(exists=True, file_okay=False))
@click.option('--overwrite', is_flag=True, help='Update items that already exist (discards changes made in flows)')
def import_command(path, overwrite):
    ''' Import from a smartcitizen-data checkout at PATH '''
    report = import_metadata(path, overwrite=overwrite)
    for kind in report.created:
        click.echo(f'{kind}: {report.created[kind]} created, {report.updated[kind]} updated, '
                   f'{report.skipped[kind]} kept')
    for error in report.errors:
        click.echo(f'Error: {error}', err=True)
    if report.errors:
        raise SystemExit(1)


@metadata_cli.command('verify')
@click.argument('source')
def verify_command(source):
    ''' Compare the served metadata with SOURCE (checkout path or base url) '''
    differences = verify_metadata(source)
    for difference in differences:
        click.echo(difference, err=True)
    if differences:
        raise SystemExit(1)
    click.echo('All served metadata matches the source')
