''' Create, update and delete metadata, recording a revision for every change

Callers commit the session.
'''
from datetime import date

from . import db
from .models import Blueprint, Calibration, Hardware, HardwareVersion, Revision
from .schemas import HardwareIn, validate_calibration

KINDS = {
    'blueprint': (Blueprint, 'name'),
    'hardware': (Hardware, 'name'),
    'calibration': (Calibration, 'sensor_id'),
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
    blueprint = find('blueprint', hardware.blueprint_name) if hardware.blueprint_name else None
    if blueprint is None:
        raise BlueprintNotInFlows(f'blueprint {hardware.blueprint_name} is not in flows' if hardware.blueprint_name
                                  else 'no blueprint')
    item = find('hardware', name)
    before = item.to_json() if item else None
    if item is None:
        item = Hardware(name=name)
        db.session.add(item)
    item.blueprint = blueprint
    item.description = hardware.description
    item.comment = hardware.comment
    item.forwarding = hardware.forwarding
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


def delete(kind, key, identity=None):
    ''' Deletes an item. Returns False if it does not exist. Raises BlueprintInUse for blueprints in use '''
    item = find(kind, key)
    if item is None:
        return False
    if kind == 'blueprint':
        used_by = list(db.session.execute(db.select(Hardware.name).filter_by(blueprint=item)
                                          .order_by(Hardware.name)).scalars())
        if used_by:
            raise BlueprintInUse(used_by)
    record(kind, key, Revision.DELETE, item.to_json(), None, identity)
    db.session.delete(item)
    return True


def revisions(kind, key):
    return db.session.execute(db.select(Revision).filter_by(kind=kind, key=key)
                              .order_by(Revision.created_at.desc(), Revision.id.desc())).scalars()
