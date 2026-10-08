''' What each user can see in the web interface

Admins see and edit all the metadata. Researchers only see (read only) the hardware
used by their devices and the calibrations of the sensors in that hardware.
'''
from . import db
from .identity import ADMIN
from .models import Calibration, Hardware


def visible_hardware(identity):
    ''' Hardware the identity can see, ordered by name '''
    query = db.select(Hardware).order_by(Hardware.name)
    if identity.role != ADMIN:
        query = query.where(Hardware.name.in_(identity.hardware))
    return db.session.execute(query).scalars().all()


def visible_sensor_ids(identity):
    ''' Sensor ids the identity can see the calibrations of. None: all '''
    if identity.role == ADMIN:
        return None
    return {sensor_id for item in visible_hardware(identity) for sensor_id in item.sensor_ids}


def visible_calibrations(identity):
    ''' Calibrations the identity can see, ordered by sensor id '''
    query = db.select(Calibration).order_by(Calibration.sensor_id)
    sensor_ids = visible_sensor_ids(identity)
    if sensor_ids is not None:
        query = query.where(Calibration.sensor_id.in_(sensor_ids))
    return db.session.execute(query).scalars().all()


def can_see(identity, kind, key):
    ''' kind: hardware, calibration, name or parameters. Names and parameters are not per device: everyone sees them '''
    if identity.role == ADMIN or kind in ('name', 'parameters'):
        return True
    if kind == 'hardware':
        return key in identity.hardware
    return key in visible_sensor_ids(identity)
