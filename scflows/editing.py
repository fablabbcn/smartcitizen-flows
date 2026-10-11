''' Create, update and delete metadata, recording a revision for every change

Callers commit the session.
'''
from datetime import date

from . import db
from .models import (Blueprint, Calibration, Hardware, HardwareBlueprint, HardwareVersion, ParameterSet, Revision,
                     SensorName)
from .schemas import HardwareIn, ParameterSetIn, SensorNameIn, validate_calibration

KINDS = {
    'blueprint': (Blueprint, 'name'),
    'hardware': (Hardware, 'name'),
    'calibration': (Calibration, 'sensor_id'),
    'name': (SensorName, 'name'),
    'parameters': (ParameterSet, 'name'),
}


def find(kind, key):
    model, field = KINDS[kind]
    return db.session.execute(db.select(model).filter_by(**{field: key})).scalar_one_or_none()


def record(kind, key, action, before, after, identity=None):
    db.session.add(Revision(kind=kind, key=key, action=action, before=before, after=after,
                            user_id=identity.id if identity else None,
                            username=identity.username if identity else None))


def validate_blueprint(body):
    # Imported here: scdata loads its configuration on import
    from scdata.models import Blueprint as BlueprintSchema
    BlueprintSchema.model_validate(body)


def save_blueprint(name, body, identity=None, action=None):
    ''' Creates or replaces a blueprint. Raises pydantic.ValidationError if invalid '''
    validate_blueprint(body)
    item = find('blueprint', name)
    before = item.to_json() if item else None
    if item is None:
        item = Blueprint(name=name)
        db.session.add(item)
    item.body = body
    record('blueprint', name, action or (Revision.UPDATE if before else Revision.CREATE), before, body, identity)
    return item, before is None


class BlueprintNotInFlows(ValueError):
    pass


class BlueprintInUse(Exception):
    def __init__(self, hardware):
        self.hardware = hardware
        super().__init__(f'Used by hardware: {", ".join(hardware)}')


def save_hardware(name, hardware, identity=None, action=None):
    '''
    Creates or replaces a hardware description from a HardwareIn.
    Raises BlueprintNotInFlows if its blueprint is not in flows
    '''
    if not isinstance(hardware, HardwareIn):
        hardware = HardwareIn.model_validate(hardware)
    names = hardware.blueprint_names
    if not names:
        raise BlueprintNotInFlows('no blueprint')
    blueprints = [find('blueprint', name) for name in names]
    missing = [name for name, blueprint in zip(names, blueprints) if blueprint is None]
    if missing:
        raise BlueprintNotInFlows(f'blueprint {", ".join(missing)} is not in flows')
    item = find('hardware', name)
    before = item.to_json() if item else None
    if item is None:
        item = Hardware(name=name)
        db.session.add(item)
    item.blueprints = blueprints
    item.description = hardware.description
    item.comment = hardware.comment
    item.forwarding = hardware.forwarding
    item.parameters = hardware.parameters or None
    item.versions = [HardwareVersion(ids=version.ids, from_date=version.from_date, to_date=version.to_date)
                     for version in sorted(hardware.versions, key=lambda version: version.from_date or date.min)]
    record('hardware', name, action or (Revision.UPDATE if before else Revision.CREATE), before, item.to_json(),
           identity)
    return item, before is None


def save_calibration(sensor_id, data, identity=None, action=None):
    ''' Creates or replaces a calibration. Raises pydantic.ValidationError if invalid '''
    kind = validate_calibration(data)
    item = find('calibration', sensor_id)
    before = item.to_json() if item else None
    if item is None:
        item = Calibration(sensor_id=sensor_id)
        db.session.add(item)
    item.kind = kind
    item.data = data
    record('calibration', sensor_id, action or (Revision.UPDATE if before else Revision.CREATE), before, data,
           identity)
    return item, before is None


class NameMismatch(ValueError):
    pass


def save_name(name, data, identity=None, action=None):
    '''
    Creates or replaces a sensor name. New names go to the end of the list (see SensorName).
    Raises pydantic.ValidationError if invalid, NameMismatch if the body names another one
    '''
    data = SensorNameIn.model_validate(data)
    if data.name is not None and data.name != name:
        raise NameMismatch(f'name: {data.name} in the body, {name} in the path')
    item = find('name', name)
    before = item.to_json() if item else None
    if item is None:
        last = db.session.execute(db.select(db.func.max(SensorName.position))).scalar()
        item = SensorName(name=name, position=(last or 0) + 1)
        db.session.add(item)
    item.sensor_id = data.id
    item.description = data.description
    item.unit = data.unit
    record('name', name, action or (Revision.UPDATE if before else Revision.CREATE), before, item.to_json(),
           identity)
    return item, before is None


class UnknownSensorType(ValueError):
    pass


def save_parameter_set(name, data, identity=None, action=None):
    '''
    Creates or replaces the parameters of a sensor type. Raises pydantic.ValidationError if invalid,
    UnknownSensorType if no parameters can apply to that type
    '''
    from .parameters import sensor_types
    if name not in sensor_types():
        raise UnknownSensorType(f'{name} is not a sensor type: {", ".join(sensor_types())}')
    data = ParameterSetIn.model_validate(data)
    item = find('parameters', name)
    before = item.to_json() if item else None
    if item is None:
        item = ParameterSet(name=name)
        db.session.add(item)
    item.channels = data.channels
    item.description = data.description
    record('parameters', name, action or (Revision.UPDATE if before else Revision.CREATE), before, item.to_json(),
           identity)
    return item, before is None


def delete(kind, key, identity=None):
    ''' Deletes an item. Returns False if it does not exist. Raises BlueprintInUse for blueprints in use '''
    item = find(kind, key)
    if item is None:
        return False
    if kind == 'blueprint':
        used_by = list(db.session.execute(db.select(Hardware.name).join(Hardware.links)
                                          .where(HardwareBlueprint.blueprint_id == item.id)
                                          .order_by(Hardware.name)).scalars())
        if used_by:
            raise BlueprintInUse(used_by)
    record(kind, key, Revision.DELETE, item.to_json(), None, identity)
    db.session.delete(item)
    return True


def revisions(kind, key):
    return db.session.execute(db.select(Revision).filter_by(kind=kind, key=key)
                              .order_by(Revision.created_at.desc(), Revision.id.desc())).scalars()
