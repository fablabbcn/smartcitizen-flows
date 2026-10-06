''' Checks of hardware descriptions against what processing needs '''
from dataclasses import dataclass, field
from datetime import date

from pydantic import ValidationError

from . import db
from .models import Blueprint, Calibration
from .schemas import HardwareIn


@dataclass
class HardwareCheck:
    hardware: HardwareIn = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def valid(self):
        return not self.errors

    def to_json(self):
        return {'valid': self.valid, 'errors': self.errors, 'warnings': self.warnings}


def slot_channels(slot, sensor_id):
    ''' Blueprint channels that processing fills for a hardware slot, as the connector does '''
    from smartcitizen_connector.tools import get_alphasense, get_pt_temp

    channels = get_alphasense(slot, sensor_id) if slot.startswith('AS') else get_pt_temp(slot, sensor_id)
    return [name for item in channels for name in item]


def check_hardware(body):
    '''
    Checks a hardware description.
    Errors make processing fail (invalid structure, blueprint not in flows, unknown slots or sensor codes,
    overlapping versions). Warnings are gaps that can be fixed later (calibrations, channels in the blueprint).
    '''
    from smartcitizen_connector._config import config as connector_config

    check = HardwareCheck()
    try:
        check.hardware = HardwareIn.model_validate(body)
    except ValidationError as error:
        check.errors += [f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
                         for item in error.errors()]
        return check

    hardware = check.hardware
    name = hardware.blueprint_name
    blueprint = db.session.execute(db.select(Blueprint).filter_by(name=name)).scalar_one_or_none() if name else None
    if name is None:
        check.errors.append('blueprint: required, the name of a blueprint in flows')
    elif blueprint is None:
        check.errors.append(f'blueprint: {name} is not in flows')
    blueprint_channels = {channel['name'] for channel in blueprint.body.get('channels', [])} if blueprint else set()

    sensor_ids = {sensor_id for version in hardware.versions for sensor_id in version.ids.values()}
    calibrated = set(db.session.execute(
        db.select(Calibration.sensor_id).where(Calibration.sensor_id.in_(sensor_ids))).scalars())

    for index, version in enumerate(hardware.versions):
        for slot, sensor_id in version.ids.items():
            where = f'versions.{index}.ids.{slot}'
            if slot[:2] not in ('AS', 'PT') or len(slot.split('_')) != 3:
                check.errors.append(f'{where}: unknown slot, expected AS_<address>_<channels> or PT_<address>_<channels>')
                continue
            if slot.startswith('AS') and sensor_id[:3] not in connector_config._as_sensor_codes:
                check.errors.append(f'{where}: unknown Alphasense sensor code {sensor_id[:3]}')
                continue
            if sensor_id not in calibrated:
                check.warnings.append(f'{where}: no calibration for {sensor_id}')
            if blueprint:
                missing = [channel for channel in slot_channels(slot, sensor_id) if channel not in blueprint_channels]
                if missing:
                    check.warnings.append(f'{where}: channels not in blueprint {name}: {", ".join(missing)}')

    versions = sorted(hardware.versions, key=lambda version: version.from_date or date.min)
    for previous, current in zip(versions, versions[1:]):
        if previous.to_date is None or current.from_date is None or current.from_date < previous.to_date:
            check.errors.append('versions: periods overlap, each version needs "to" before the next "from"')
            break

    return check
