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

from . import db
from .models import Blueprint, Calibration, Hardware, HardwareVersion

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


def validate_blueprint(body):
    # Imported here: scdata loads its configuration on import
    from scdata.models import Blueprint as BlueprintSchema
    BlueprintSchema.model_validate(body)


def validate_hardware(body):
    from smartcitizen_connector.models import HardwarePostprocessing
    HardwarePostprocessing.model_validate(body)


def upsert(model, key, value, overwrite, report, kind):
    ''' Returns the item to fill in, or None if it exists and should be kept '''
    item = db.session.execute(db.select(model).filter_by(**{key: value})).scalar_one_or_none()
    if item is None:
        item = model(**{key: value})
        db.session.add(item)
        report.created[kind] += 1
    elif overwrite:
        report.updated[kind] += 1
    else:
        report.skipped[kind] += 1
        return None
    return item


def import_metadata(path, overwrite=False):
    '''
    Imports blueprints, hardware and calibrations from a smartcitizen-data checkout.
    Existing items are kept unless overwrite is set. Invalid files are reported and skipped.
    '''
    report = ImportReport()

    for blueprint_path in sorted(glob(join(path, 'blueprints', '*.json'))):
        body = load_json(blueprint_path)
        try:
            validate_blueprint(body)
        except ValidationError as error:
            report.errors.append(f'{blueprint_path}: {error}')
            continue
        item = upsert(Blueprint, 'name', name_of(blueprint_path), overwrite, report, 'blueprints')
        if item is not None:
            item.body = body
    db.session.flush()

    blueprints = {blueprint.name: blueprint for blueprint in db.session.execute(db.select(Blueprint)).scalars()}

    for hardware_path in sorted(glob(join(path, 'hardware', '*.json'))):
        body = load_json(hardware_path)
        try:
            validate_hardware(body)
            versions = [HardwareVersion(ids=version['ids'],
                                        from_date=HardwareVersion.parse_date(version.get('from')),
                                        to_date=HardwareVersion.parse_date(version.get('to')))
                        for version in body.get('versions', [])]
        except (ValidationError, ValueError, KeyError) as error:
            report.errors.append(f'{hardware_path}: {error!r}')
            continue
        item = upsert(Hardware, 'name', name_of(hardware_path), overwrite, report, 'hardware')
        if item is None:
            continue
        item.blueprint_url = body.get('blueprint_url')
        item.blueprint = blueprints.get(name_of(item.blueprint_url)) if item.blueprint_url else None
        item.description = body.get('description')
        item.comment = body.get('comment')
        item.forwarding = body.get('forwarding')
        item.versions = versions

    calibrations_path = join(path, 'calibrations', 'calibrations.json')
    for sensor_id, data in load_json(calibrations_path).items():
        if not isinstance(data, dict):
            report.errors.append(f'{calibrations_path}: {sensor_id} is not an object')
            continue
        item = upsert(Calibration, 'sensor_id', sensor_id, overwrite, report, 'calibrations')
        if item is not None:
            item.kind = Calibration.kind_of(data)
            item.data = data

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

    def compare(served_path, relative_path):
        served = client.get(f'/api/v1/{served_path}').get_json()
        expected = read_source(source, relative_path)
        if expected is None:
            differences.append(f'{relative_path}: not in source')
        elif served != expected:
            differences.append(f'{relative_path}: served content differs')

    for blueprint in db.session.execute(db.select(Blueprint)).scalars():
        compare(f'blueprints/{blueprint.name}.json', f'blueprints/{blueprint.name}.json')
    for hardware in db.session.execute(db.select(Hardware)).scalars():
        compare(f'hardware/{hardware.name}.json', f'hardware/{hardware.name}.json')
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
