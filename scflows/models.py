from datetime import date, datetime, timezone

from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import JSONB

from . import db

# JSONB on PostgreSQL, JSON elsewhere (tests use SQLite)
JSONType = db.JSON().with_variant(JSONB(), 'postgresql')


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True) # primary keys are required by SQLAlchemy
    # email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    name = db.Column(db.String(1000))


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Blueprint(TimestampMixin, db.Model):
    ''' Processing blueprint (smartcitizen-data/blueprints/<name>.json) '''
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    body = db.Column(JSONType, nullable=False)

    def to_json(self):
        return self.body


class Hardware(TimestampMixin, db.Model):
    ''' Hardware description (smartcitizen-data/hardware/<name>.json) '''
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.Text)
    comment = db.Column(db.Text)
    forwarding = db.Column(db.String(64))
    # Served as stored. blueprint_id links it when the blueprint is in flows
    blueprint_url = db.Column(db.Text)
    blueprint_id = db.Column(db.Integer, db.ForeignKey('blueprint.id', ondelete='SET NULL'))
    blueprint = db.relationship('Blueprint')
    versions = db.relationship('HardwareVersion', back_populates='hardware', cascade='all, delete-orphan',
                               order_by='HardwareVersion.from_date')

    def to_json(self):
        ''' Same structure as the hardware files: optional keys are left out when empty '''
        result = {'blueprint_url': self.blueprint_url, 'description': self.description}
        if self.comment is not None:
            result['comment'] = self.comment
        if self.forwarding is not None:
            result['forwarding'] = self.forwarding
        result['versions'] = [version.to_json() for version in self.versions]
        return result


class HardwareVersion(db.Model):
    ''' Sensor ids mounted in a hardware slot during a period '''
    id = db.Column(db.Integer, primary_key=True)
    hardware_id = db.Column(db.Integer, db.ForeignKey('hardware.id', ondelete='CASCADE'), nullable=False)
    hardware = db.relationship('Hardware', back_populates='versions')
    from_date = db.Column(db.Date)
    to_date = db.Column(db.Date)
    ids = db.Column(JSONType, nullable=False)

    def to_json(self):
        return {
            'ids': self.ids,
            'from': self.from_date.isoformat() if self.from_date else None,
            'to': self.to_date.isoformat() if self.to_date else None,
        }

    @staticmethod
    def parse_date(value):
        return date.fromisoformat(value) if value else None


class Calibration(TimestampMixin, db.Model):
    ''' Calibration data of a sensor or board (smartcitizen-data/calibrations/calibrations.json) '''
    ALPHASENSE_SENSOR = 'alphasense_sensor'
    AFE_BOARD = 'afe_board'

    id = db.Column(db.Integer, primary_key=True)
    sensor_id = db.Column(db.String(64), unique=True, nullable=False)
    kind = db.Column(db.String(32), nullable=False, index=True)
    data = db.Column(JSONType, nullable=False)

    @classmethod
    def kind_of(cls, data):
        return cls.AFE_BOARD if {'t20', 'v20'} <= set(data) else cls.ALPHASENSE_SENSOR

    def to_json(self):
        return self.data
