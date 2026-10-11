import copy
import json
from os.path import join

import pytest

from scflows.metadata import import_metadata
from scflows.validation import check_hardware

from conftest import DATA

with open(join(DATA, 'hardware', 'SCAS_TEST1.json')) as file:
    HARDWARE = json.load(file)


@pytest.fixture
def hardware(app):
    import_metadata(DATA)
    return copy.deepcopy(HARDWARE)


def test_valid_hardware(hardware):
    check = check_hardware(hardware)

    assert check.valid
    # The test blueprint only has NO2 and NO2_WE
    assert check.warnings == ['versions.0.ids.AS_48_32: channels not in blueprint test_air: NO2_AE',
                              'versions.0.ids.PT_49_23: channels not in blueprint test_air: ASPT1000, PT1000_POS']


def test_unknown_fields_and_bad_dates(hardware):
    hardware['owner'] = 'me'
    hardware['versions'][0]['from'] = '2024-13-01'

    check = check_hardware(hardware)

    assert not check.valid
    errors = sorted(check.errors)
    assert errors[0] == 'owner: Extra inputs are not permitted'
    assert errors[1].startswith('versions.0.from: Input should be a valid date')


def test_from_after_to(hardware):
    hardware['versions'][0]['to'] = '2024-01-01'

    assert check_hardware(hardware).errors == ['versions.0: Value error, "from" must be earlier than "to"']


def test_unknown_slot_and_sensor_code(hardware):
    hardware['versions'][0]['ids'] = {'XX_48_01': '1', 'AS_48_32': '730002320'}

    assert check_hardware(hardware).errors == [
        'versions.0.ids.XX_48_01: unknown slot, expected AS_<address>_<channels> or PT_<address>_<channels>',
        'versions.0.ids.AS_48_32: unknown Alphasense sensor code 730',
    ]


def test_short_sensor_id(hardware):
    hardware['versions'][0]['ids'] = {'AS_48_32': ''}

    assert check_hardware(hardware).errors == [
        "versions.0.ids.AS_48_32: sensor id '' is too short to hold an Alphasense code"]


def test_missing_calibration(hardware):
    hardware['versions'][0]['ids'] = {'AS_48_32': '212999999'}

    check = check_hardware(hardware)

    assert check.valid
    assert check.warnings == ['versions.0.ids.AS_48_32: no calibration for 212999999',
                              'versions.0.ids.AS_48_32: channels not in blueprint test_air: NO2_AE']


@pytest.mark.parametrize('change, error', [
    ({'blueprint_url': None}, 'blueprint: required, the name of a blueprint in flows'),
    ({'blueprint_url': 'https://example.com/blueprints/other.json'}, 'blueprint: other is not in flows'),
    ({'blueprint_url': None, 'blueprint': 'other'}, 'blueprint: other is not in flows'),
])
def test_blueprint_must_be_in_flows(hardware, change, error):
    hardware.update(change)

    assert check_hardware(hardware).errors == [error]


def test_overlapping_versions(hardware):
    hardware['versions'].append({'ids': {'AS_48_32': '212830246'}, 'from': '2025-01-01', 'to': None})

    assert check_hardware(hardware).errors == [
        'versions: periods overlap, each version needs "to" before the next "from"']

    hardware['versions'][0]['to'] = '2025-01-01'
    assert check_hardware(hardware).valid


def test_blueprint_and_url_must_match(hardware):
    hardware['blueprint'] = 'other'

    assert check_hardware(hardware).errors == [
        'body: Value error, "blueprint" and "blueprint_url" refer to different blueprints']
