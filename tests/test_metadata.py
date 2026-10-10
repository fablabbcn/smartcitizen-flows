import json
import shutil

import pytest

from scflows import db
from scflows.metadata import import_metadata, verify_metadata
from scflows.models import Blueprint, Calibration, Hardware

from conftest import DATA


@pytest.fixture
def source(tmp_path):
    ''' Writable copy of the test smartcitizen-data checkout '''
    path = tmp_path / 'smartcitizen-data'
    shutil.copytree(DATA, path)
    return path


def edit_json(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_import(app, source):
    report = import_metadata(str(source))

    assert report.errors == []
    assert report.created == {'blueprints': 1, 'hardware': 2, 'calibrations': 2, 'names': 7}
    hardware = db.session.execute(db.select(Hardware).filter_by(name='SCAS_TEST1')).scalar_one()
    assert hardware.blueprint.name == 'test_air'
    assert hardware.versions[0].from_date.isoformat() == '2024-04-01'
    kinds = dict(db.session.execute(db.select(Calibration.sensor_id, Calibration.kind)).all())
    assert kinds == {'212830246': 'alphasense_sensor', '10-002911': 'afe_board'}


def test_import_keeps_existing_items(app, source):
    import_metadata(str(source))
    edit_json(source / 'hardware' / 'SCAS_TEST1.json', lambda data: data.update(description='Changed'))

    report = import_metadata(str(source))

    assert report.created == {'blueprints': 0, 'hardware': 0, 'calibrations': 0, 'names': 0}
    assert report.skipped == {'blueprints': 1, 'hardware': 2, 'calibrations': 2, 'names': 7}
    assert db.session.execute(db.select(Hardware.description).filter_by(name='SCAS_TEST1')).scalar_one() \
        == '1SEN55-2ELEC-AFE'


def test_import_overwrite(app, source):
    import_metadata(str(source))
    edit_json(source / 'hardware' / 'SCAS_TEST1.json', lambda data: data['versions'].append(
        {'ids': {'AS_48_32': '214920348'}, 'from': '2025-01-01', 'to': None}))

    report = import_metadata(str(source), overwrite=True)

    assert report.updated['hardware'] == 2
    hardware = db.session.execute(db.select(Hardware).filter_by(name='SCAS_TEST1')).scalar_one()
    assert [version.ids['AS_48_32'] for version in hardware.versions] == ['212830246', '214920348']


def test_import_rejects_blueprints_not_in_flows(app, source):
    edit_json(source / 'hardware' / 'SCAS_TEST2.json', lambda data: data.update(
        blueprint_url='https://raw.githubusercontent.com/fablabbcn/smartcitizen-data/master/blueprints/sck_21.json'))

    report = import_metadata(str(source))

    assert report.created['hardware'] == 1
    assert report.errors == [f"{source / 'hardware' / 'SCAS_TEST2.json'}: blueprint sck_21 is not in flows"]


def test_import_reports_invalid_files(app, source):
    (source / 'hardware' / 'SCAS_BAD_DATE.json').write_text(json.dumps(
        {'blueprint_url': None, 'description': 'x', 'versions': [{'ids': {}, 'from': '2024-13-01', 'to': None}]}))
    (source / 'blueprints' / 'bad.json').write_text(json.dumps({'channels': [{'name': 'missing function'}]}))

    report = import_metadata(str(source))

    assert len(report.errors) == 2
    assert db.session.execute(db.select(Hardware).filter_by(name='SCAS_BAD_DATE')).scalar_one_or_none() is None
    assert db.session.execute(db.select(Blueprint).filter_by(name='bad')).scalar_one_or_none() is None


def test_verify_matches_source(app, source):
    import_metadata(str(source))

    assert verify_metadata(str(source)) == []


def test_verify_reports_differences(app, source):
    import_metadata(str(source))
    edit_json(source / 'calibrations' / 'calibrations.json', lambda data: data['10-002911'].update(v20='0.3'))
    (source / 'hardware' / 'SCAS_NEW.json').write_text((source / 'hardware' / 'SCAS_TEST1.json').read_text())
    (source / 'hardware' / 'SCAS_TEST2.json').unlink()

    assert sorted(verify_metadata(str(source))) == [
        'calibrations/calibrations.json: served content differs',
        'hardware/SCAS_NEW.json: not served',
        'hardware/SCAS_TEST2.json: not in source',
    ]


def test_cli(app, source):
    runner = app.test_cli_runner()

    result = runner.invoke(args=['metadata', 'import', str(source)])
    assert result.exit_code == 0
    assert 'hardware: 2 created, 0 updated, 0 kept' in result.output

    result = runner.invoke(args=['metadata', 'verify', str(source)])
    assert result.exit_code == 0

    (source / 'hardware' / 'SCAS_TEST1.json').write_text('{"description": "x", "versions": [{"ids": {}, "from": "bad"}]}')
    result = runner.invoke(args=['metadata', 'import', str(source), '--overwrite'])
    assert result.exit_code == 1


def test_malformed_files_are_reported_and_skipped(app, source):
    (source / 'hardware' / 'SCAS_TEST2.json').write_text('{"blueprint_url": ')

    report = import_metadata(str(source))

    assert len(report.errors) == 1 and 'SCAS_TEST2.json: cannot be read as JSON' in report.errors[0]
    assert report.created['hardware'] == 1 and report.created['calibrations'] == 2
