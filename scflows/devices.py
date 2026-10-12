''' Links between devices, their jobs, hardware and calibrations, for the pages of each '''
import os
from urllib.parse import quote

from . import db
from .health import device_ids_of
from .models import Calibration, DeviceHealth, Hardware, Job, JobRun


def uncalibrated(hardware_list):
    ''' {hardware name: sensor ids of its versions without calibration in flows} '''
    ids = {sensor_id for hardware in hardware_list for sensor_id in hardware.sensor_ids}
    calibrated = set(db.session.execute(db.select(Calibration.sensor_id).where(Calibration.sensor_id.in_(ids))).scalars())
    return {hardware.name: [sensor_id for sensor_id in hardware.sensor_ids if sensor_id not in calibrated]
            for hardware in hardware_list}


def hardware_by_name(names):
    names = {name for name in names if name}
    if not names:
        return {}
    return {item.name: item for item in db.session.execute(db.select(Hardware).where(Hardware.name.in_(names))).scalars()}


def devices(identity, hardware_names=None):
    '''
    Devices with jobs, as [{device_id, name, hardware, jobs}], of some hardware or all, that the identity
    can see (admins all, researchers their own)
    '''
    query = db.select(Job).order_by(Job.device_id, Job.task)
    if hardware_names is not None:
        query = query.where(Job.hardware.in_(list(hardware_names)))
    visible = device_ids_of(identity)
    result = {}
    for job in db.session.execute(query).scalars():
        if visible is not None and job.device_id not in visible:
            continue
        item = result.setdefault(job.device_id, {'device_id': job.device_id, 'name': job.device_name,
                                                 'hardware': job.hardware, 'jobs': []})
        item['name'] = item['name'] or job.device_name
        item['hardware'] = item['hardware'] or job.hardware
        item['jobs'].append(job)
    return list(result.values())


def hardware_using(sensor_id, identity):
    ''' [(hardware, version, slot)] whose versions mount a sensor id, of the hardware the identity can see '''
    from .access import visible_hardware
    result = []
    for hardware in visible_hardware(identity):
        for version in hardware.versions:
            for slot, value in version.ids.items():
                if value == sensor_id:
                    result.append((hardware, version, slot))
    return result


def device_name(device_id):
    ''' Name from the jobs (the sync), or else from the latest health check '''
    name = db.session.execute(db.select(Job.device_name).where(Job.device_id == device_id, Job.device_name.is_not(None))
                              .limit(1)).scalar()
    if name:
        return name
    return db.session.execute(db.select(DeviceHealth.device_name).where(DeviceHealth.device_id == device_id,
                                                                        DeviceHealth.device_name.is_not(None))
                              .order_by(DeviceHealth.id.desc()).limit(1)).scalar()


def device_hardware_name(device_id):
    ''' Hardware from the jobs (the sync), or else from the latest run that saw it '''
    name = db.session.execute(db.select(Job.hardware).where(Job.device_id == device_id, Job.hardware.is_not(None))
                              .limit(1)).scalar()
    if name:
        return name
    for run in db.session.execute(db.select(JobRun).where(JobRun.device_id == device_id, JobRun.context.is_not(None))
                                  .order_by(JobRun.id.desc()).limit(10)).scalars():
        if (run.context or {}).get('hardware'):
            return run.context['hardware']
    return None


def backup_location(device_id):
    ''' Where the backups of a device are (dbackup writes them to S3) '''
    bucket = os.environ.get('S3_DATA_BUCKET')
    return f's3://{bucket}/devices/{device_id}/' if bucket else None


def storage_link(location):
    ''' Link to browse a storage location: the S3 console for s3://bucket/prefix, nothing for a local folder '''
    if not location or not location.startswith('s3://'):
        return None
    bucket, _, prefix = location[len('s3://'):].partition('/')
    if not bucket:
        return None
    return f'https://s3.console.aws.amazon.com/s3/buckets/{quote(bucket)}?prefix={quote(prefix)}&showversions=false'
