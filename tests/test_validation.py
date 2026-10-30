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


def test_afe_board_in_an_as_slot(hardware):
    hardware['versions'][0]['ids'] = {'AS_48_32': '212830246', 'AS_49_32': '10-002911'}

    check = check_hardware(hardware)

    assert check.valid
    # Its PT1000, as in a PT slot, with the channels in the AS slot order
    assert 'versions.0.ids.AS_49_32: channels not in blueprint test_air: ASPT1000, PT1000_POS' in check.warnings


def test_two_afe_boards_in_a_version(hardware):
    hardware['versions'][0]['ids']['AS_49_10'] = '17-000123'

    check = check_hardware(hardware)

    assert check.valid
    assert 'versions.0.ids: AFE boards in PT_49_23, AS_49_10, only one fills the PT1000 temperature (ASPT1000)' \
        in check.warnings


def test_slot_channels_of_an_afe_board_in_an_as_slot():
    from scflows.validation import slot_channels

    assert slot_channels('AS_49_32', '17-000123') == ['ASPT1000', 'PT1000_POS']


@pytest.mark.parametrize('slot', ['ASX_48_32', 'AS_48', 'AS_48_3', 'PT_49_23_1'])
def test_slots_are_checked_whole(hardware, slot):
    hardware['versions'][0]['ids'] = {slot: '212830246'}

    assert check_hardware(hardware).errors == [
        f'versions.0.ids.{slot}: unknown slot, expected AS_<address>_<channels> or PT_<address>_<channels>']


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
    ({'blueprints': []}, 'blueprints: required, one or two blueprints in flows'),
    ({'blueprints': ['other']}, 'blueprints: other is not in flows'),
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


@pytest.mark.parametrize('key', ['blueprint', 'blueprint_url'])
def test_only_the_list_of_blueprints(hardware, key):
    hardware[key] = 'test_air'

    assert check_hardware(hardware).errors == [f'{key}: Extra inputs are not permitted']
