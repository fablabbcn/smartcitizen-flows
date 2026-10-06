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
    # Blueprint in flows. blueprint_url is only kept for blueprints that are not in flows
    blueprint_url = db.Column(db.Text)
    blueprint_id = db.Column(db.Integer, db.ForeignKey('blueprint.id', ondelete='SET NULL'))
    blueprint = db.relationship('Blueprint')
    versions = db.relationship('HardwareVersion', back_populates='hardware', cascade='all, delete-orphan',
                               order_by='HardwareVersion.from_date')

    def to_json(self, blueprint_url=None):
        '''
        Same structure as the hardware files, plus the blueprint name. Optional keys are left out when empty.
        blueprint_url: url of the linked blueprint in flows (built by the API)
        '''
        result = {'blueprint': self.blueprint.name if self.blueprint else None,
                  'blueprint_url': blueprint_url if self.blueprint and blueprint_url else self.blueprint_url,
                  'description': self.description}
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


class Revision(db.Model):
    ''' History of changes to blueprints, hardware and calibrations '''
    CREATE = 'create'
    UPDATE = 'update'
    DELETE = 'delete'
    IMPORT = 'import'

    id = db.Column(db.Integer, primary_key=True)
    # blueprint, hardware or calibration, and its name or sensor_id
    kind = db.Column(db.String(32), nullable=False)
    key = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(16), nullable=False)
    before = db.Column(JSONType)
    after = db.Column(JSONType)
    # Smart Citizen user, empty for imports
    user_id = db.Column(db.Integer)
    username = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (db.Index('ix_revision_kind_key', 'kind', 'key'),)

    def to_json(self):
        return {
            'id': self.id,
            'action': self.action,
            'username': self.username,
            'created_at': self.created_at.isoformat(),
            'before': self.before,
            'after': self.after,
        }
