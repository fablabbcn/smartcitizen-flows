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
    '''
    Processing blueprint (smartcitizen-data/blueprints/<name>.json). Its kind (meta.kind) says how
    it is used: process (every few hours, latest data from the Smart Citizen API, posted back), long
    (every few days, a long window of the backups, results in S3) or backup (backups only)
    '''
    PROCESS = 'process'
    LONG = 'long'
    BACKUP = 'backup'
    KINDS = (PROCESS, LONG, BACKUP)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    body = db.Column(JSONType, nullable=False)

    @property
    def meta(self):
        return self.body.get('meta') or {}

    @property
    def kind(self):
        ''' Blueprints without meta.kind are processing blueprints '''
        return self.meta.get('kind') or self.PROCESS

    def to_json(self):
        return self.body


class HardwareBlueprint(db.Model):
    ''' Blueprints of a hardware, in order. Blueprints in use cannot be deleted '''
    hardware_id = db.Column(db.Integer, db.ForeignKey('hardware.id', ondelete='CASCADE'), primary_key=True)
    blueprint_id = db.Column(db.Integer, db.ForeignKey('blueprint.id', ondelete='RESTRICT'), primary_key=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    blueprint = db.relationship('Blueprint')


class Hardware(TimestampMixin, db.Model):
    ''' Hardware description (smartcitizen-data/hardware/<name>.json) '''
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.Text)
    comment = db.Column(db.Text)
    forwarding = db.Column(db.String(64))
    # Long processing parameters for the devices of this hardware: {channel: kwargs}
    parameters = db.Column(JSONType)
    # One or two blueprints in flows, of different kinds (see Blueprint)
    links = db.relationship('HardwareBlueprint', cascade='all, delete-orphan', order_by='HardwareBlueprint.position')
    versions = db.relationship('HardwareVersion', back_populates='hardware', cascade='all, delete-orphan',
                               order_by='HardwareVersion.from_date')

    @property
    def blueprints(self):
        return [link.blueprint for link in self.links]

    @blueprints.setter
    def blueprints(self, blueprints):
        # Links kept for blueprints already in the list: a new one with the same key would clash on flush
        existing = {link.blueprint_id: link for link in self.links if link.blueprint_id is not None}
        links = []
        for position, blueprint in enumerate(blueprints):
            link = existing.get(blueprint.id) or HardwareBlueprint(blueprint=blueprint)
            link.position = position
            links.append(link)
        self.links = links

    def blueprint_of(self, kind):
        return next((blueprint for blueprint in self.blueprints if blueprint.kind == kind), None)

    @property
    def kinds(self):
        return [blueprint.kind for blueprint in self.blueprints]

    @property
    def sensor_ids(self):
        ''' Sensor ids of all versions, without repetitions '''
        return list(dict.fromkeys(sensor_id for version in self.versions for sensor_id in version.ids.values()))

    def to_json(self):
        ''' The hardware description, with all its keys (the API adds its name and url) '''
        return {'blueprints': [blueprint.name for blueprint in self.blueprints],
                'description': self.description,
                'comment': self.comment,
                'forwarding': self.forwarding,
                'parameters': self.parameters or {},
                'versions': [version.to_json() for version in self.versions]}


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
    # Alphasense order it came with. Not served with the data: processing reads every value as a number
    order_number = db.Column(db.String(64))

    @classmethod
    def kind_of(cls, data):
        return cls.AFE_BOARD if {'t20', 'v20'} <= set(data) else cls.ALPHASENSE_SENSOR

    def to_json(self):
        return self.data

    def history_json(self):
        ''' As the history keeps it: the data and the order number '''
        return {**self.data, 'order_number': self.order_number} if self.order_number else self.data


class SensorName(TimestampMixin, db.Model):
    '''
    Name that scdata (and the blueprints) give a Smart Citizen sensor id
    (smartcitizen-data/names/SCDevice.json). Served in order: for an id with several
    names, scdata uses the first one
    '''
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    # Smart Citizen API sensor id. 0: no id in the platform
    sensor_id = db.Column(db.Integer, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=False, default='')
    unit = db.Column(db.String(32), nullable=False, default='')
    position = db.Column(db.Integer, nullable=False, index=True)

    def to_json(self):
        return {'name': self.name, 'id': self.sensor_id, 'description': self.description, 'unit': self.unit}


class ParameterSet(TimestampMixin, db.Model):
    ''' Parameters of long processing for a sensor type: {channel: kwargs} (see parameters.py) '''
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.Text)
    channels = db.Column(JSONType, nullable=False)

    def to_json(self):
        result = {'channels': self.channels}
        if self.description:
            result['description'] = self.description
        return result


class Attachment(db.Model):
    ''' A file attached to a hardware (see attachments.py). By name: files stay when the hardware is deleted '''
    id = db.Column(db.Integer, primary_key=True)
    hardware = db.Column(db.String(64), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(128), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    # s3://<bucket>/attachments/hardware/<name>/<random>-<filename>, or under STORAGE_ROOT
    location = db.Column(db.String(512), nullable=False)
    description = db.Column(db.String(255), nullable=False, default='')
    user_id = db.Column(db.Integer)
    username = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def to_json(self):
        return {'id': self.id, 'hardware': self.hardware, 'filename': self.filename,
                'content_type': self.content_type, 'size': self.size, 'description': self.description,
                'username': self.username, 'created_at': self.created_at.isoformat() if self.created_at else None}


class Revision(db.Model):
    ''' History of changes to blueprints, hardware, calibrations and sensor names '''
    CREATE = 'create'
    UPDATE = 'update'
    DELETE = 'delete'
    IMPORT = 'import'

    id = db.Column(db.Integer, primary_key=True)
    # blueprint, hardware, calibration or name, and its name or sensor_id
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
    # Long processing: baselines over months of backups (blueprints of kind long)
    LONG = 'long'
    TASKS = (PROCESS, BACKUP, LONG)
    # Created by the sync with the Smart Citizen API, or by an admin
    AUTO = 'auto'
    MANUAL = 'manual'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, nullable=False)
    # From the Smart Citizen API, updated by the sync: name and hardware (its postprocessing hardware_url)
    device_name = db.Column(db.String(255))
    hardware = db.Column(db.String(64))
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
            'id': self.id, 'device_id': self.device_id, 'device_name': self.device_name, 'hardware': self.hardware,
            'task': self.task,
            'source': self.source,
            'enabled': self.enabled, 'paused': self.paused, 'interval_hours': self.interval_hours,
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
            'last_queued_at': self.last_queued_at.isoformat() if self.last_queued_at else None,
        }


class JobRun(db.Model):
    ''' One execution of a job, of a task requested once, or of a sync of the jobs (task sync, no device) '''
    SYNC = 'sync'
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    ABORTED = 'aborted'

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('job.id', ondelete='SET NULL'), index=True)
    job = db.relationship('Job', back_populates='runs')
    # Empty for syncs
    device_id = db.Column(db.Integer, index=True)
    task = db.Column(db.String(16), nullable=False)
    dry_run = db.Column(db.Boolean, nullable=False, default=False)
    state = db.Column(db.String(16), nullable=False, default=QUEUED)
    # Result code of the task, e.g. PROCESSED AND UPLOADED, EMPTY_DATA
    message = db.Column(db.Text)
    log = db.Column(JSONType)
    # What the run used: device name, hardware, blueprint, calibrations, period, storage (see scflows.tasks.context)
    context = db.Column(JSONType)
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
            result['context'] = self.context
        return result


class DeviceHealth(db.Model):
    '''
    Health checks of the blueprint (gaps, implausible, flat values, outliers) over the data of a
    processing run: scdata's device.health, with a status per column, check and device (see health.py)
    '''
    OK = 'ok'
    WARNING = 'warning'
    PROBLEM = 'problem'
    ERROR = 'error'
    # Worst last
    STATUSES = (OK, WARNING, PROBLEM, ERROR)

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, nullable=False, index=True)
    device_name = db.Column(db.String(255))
    blueprint = db.Column(db.String(64))
    run_id = db.Column(db.Integer, db.ForeignKey('job_run.id', ondelete='SET NULL'), index=True)
    run = db.relationship('JobRun')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    # Period of the data checked
    start = db.Column(db.DateTime(timezone=True))
    end = db.Column(db.DateTime(timezone=True))
    rows = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(16), nullable=False)
    checks = db.Column(JSONType, nullable=False)

    def to_json(self, checks=True):
        result = {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device_name,
            'blueprint': self.blueprint,
            'run_id': self.run_id,
            'created_at': self.created_at.isoformat(),
            'start': self.start.isoformat() if self.start else None,
            'end': self.end.isoformat() if self.end else None,
            'rows': self.rows,
            'status': self.status,
        }
        if checks:
            result['checks'] = self.checks
        return result
