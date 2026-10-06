from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from . import db

# JSONB on PostgreSQL, JSON elsewhere (tests use SQLite)
JSONType = db.JSON().with_variant(JSONB(), 'postgresql')


def utcnow():
    return datetime.now(timezone.utc)


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
    # Every hardware uses a blueprint in flows: blueprints in use cannot be deleted
    blueprint_id = db.Column(db.Integer, db.ForeignKey('blueprint.id', ondelete='RESTRICT'), nullable=False)
    blueprint = db.relationship('Blueprint')
    versions = db.relationship('HardwareVersion', back_populates='hardware', cascade='all, delete-orphan',
                               order_by='HardwareVersion.from_date')

    def to_json(self, blueprint_url=None):
        '''
        Same structure as the hardware files, with the blueprint name. Optional keys are left out when empty.
        blueprint_url: url of the blueprint in flows (built by the API)
        '''
        result = {'blueprint': self.blueprint.name}
        if blueprint_url:
            result['blueprint_url'] = blueprint_url
        result['description'] = self.description
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


class Job(TimestampMixin, db.Model):
    ''' Periodic task for a device: process (dprocess) or backup (dbackup) '''
    PROCESS = 'process'
    BACKUP = 'backup'
    TASKS = (PROCESS, BACKUP)
    # Created by the sync with the Smart Citizen API, or by an admin
    AUTO = 'auto'
    MANUAL = 'manual'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, nullable=False)
    task = db.Column(db.String(16), nullable=False)
    source = db.Column(db.String(16), nullable=False, default=AUTO)
    # enabled: the device qualifies (set by the sync). paused: stopped by an admin
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    paused = db.Column(db.Boolean, nullable=False, default=False)
    interval_hours = db.Column(db.Integer, nullable=False)
    next_run_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    last_queued_at = db.Column(db.DateTime(timezone=True))
    runs = db.relationship('JobRun', back_populates='job', order_by='JobRun.id.desc()', lazy='dynamic')

    __table_args__ = (db.UniqueConstraint('device_id', 'task', name='uq_job_device_task'),)

    @property
    def active(self):
        return self.enabled and not self.paused

    def to_json(self):
        return {
            'id': self.id, 'device_id': self.device_id, 'task': self.task, 'source': self.source,
            'enabled': self.enabled, 'paused': self.paused, 'interval_hours': self.interval_hours,
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
            'last_queued_at': self.last_queued_at.isoformat() if self.last_queued_at else None,
        }


class JobRun(db.Model):
    ''' One execution of a job, or of a task requested once '''
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    ABORTED = 'aborted'

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('job.id', ondelete='SET NULL'), index=True)
    job = db.relationship('Job', back_populates='runs')
    device_id = db.Column(db.Integer, nullable=False, index=True)
    task = db.Column(db.String(16), nullable=False)
    dry_run = db.Column(db.Boolean, nullable=False, default=False)
    state = db.Column(db.String(16), nullable=False, default=QUEUED)
    # Result code of the task, e.g. PROCESSED AND UPLOADED, EMPTY_DATA
    message = db.Column(db.Text)
    log = db.Column(JSONType)
    # Smart Citizen user who requested it, empty for scheduled runs
    username = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))

    def to_json(self, log=False):
        result = {
            'id': self.id, 'job_id': self.job_id, 'device_id': self.device_id, 'task': self.task,
            'dry_run': self.dry_run, 'state': self.state, 'message': self.message, 'username': self.username,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
        }
        if log:
            result['log'] = self.log
        return result
