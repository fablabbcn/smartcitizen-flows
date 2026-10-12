''' Results of long processing, in the same storage as the backups

devices/<id>/processed/<blueprint>/month=YYYY-MM/*.parquet, one partition per month, and the
information of the last run in devices/<id>/processed/<blueprint>.json (outside the dataset, which
must only hold parquet files). Each run
recomputes its whole window (baselines change with more data), so the months it covers are
replaced. Windows start on the first day of a month, so a month is never replaced in part.
'''
import json
from datetime import datetime, timezone
from os import environ

import pandas as pd


def root():
    ''' s3://<S3_DATA_BUCKET>, or STORAGE_ROOT (e.g. a local folder) '''
    if environ.get('STORAGE_ROOT'):
        return environ['STORAGE_ROOT'].rstrip('/')
    return f"s3://{environ['S3_DATA_BUCKET']}"


def filesystem(location):
    ''' pyarrow filesystem and path of a location (s3://bucket/path or a local folder) '''
    from pyarrow import fs
    if location.startswith('s3://'):
        region = environ.get('AWS_REGION') or environ.get('AWS_DEFAULT_REGION')
        return (fs.S3FileSystem(region=region) if region else fs.S3FileSystem()), location[len('s3://'):]
    return fs.LocalFileSystem(), location


def processed_location(device_id, blueprint):
    return f'{root()}/devices/{device_id}/processed/{blueprint}'


def month_start(value):
    value = pd.Timestamp(value)
    return value.normalize().replace(day=1)


def write_processed(device_id, blueprint, data, info):
    '''
    Stores the result of a long run (index: TIME), replacing the months it covers, and info
    (window, parameters...) of the run. Returns the months written
    '''
    import pyarrow as pa
    import pyarrow.dataset as ds

    frame = data.copy()
    frame.index.name = 'TIME'
    frame = frame.reset_index()
    frame['month'] = frame['TIME'].dt.strftime('%Y-%m')
    table = pa.Table.from_pandas(frame, preserve_index=False)
    fs, path = filesystem(processed_location(device_id, blueprint))
    ds.write_dataset(table, path, filesystem=fs, format='parquet', partitioning=['month'],
                     partitioning_flavor='hive', existing_data_behavior='delete_matching',
                     basename_template='part-{i}.parquet')
    with fs.open_output_stream(f'{path}.json') as stream:
        stream.write(json.dumps(dict(info, written_at=datetime.now(timezone.utc).isoformat()), default=str).encode())
    return sorted(frame['month'].unique())


def read_run_info(device_id, blueprint):
    fs, path = filesystem(processed_location(device_id, blueprint))
    try:
        with fs.open_input_stream(f'{path}.json') as stream:
            return json.loads(stream.read())
    except (FileNotFoundError, OSError):
        return None


def read_processed(device_id, blueprint, channels=None, start=None, end=None):
    ''' Result of the long runs of a device in [start, end] (index: TIME), or None if there is none '''
    import pyarrow as pa
    import pyarrow.dataset as ds

    fs, path = filesystem(processed_location(device_id, blueprint))
    try:
        dataset = ds.dataset(path, filesystem=fs, format='parquet', partitioning='hive')
    except (FileNotFoundError, OSError):
        return None
    if 'TIME' not in dataset.schema.names:
        return None
    time_type = dataset.schema.field('TIME').type
    expression = None
    for value, compare in ((start, lambda field, scalar: field >= scalar), (end, lambda field, scalar: field <= scalar)):
        if value is None:
            continue
        timestamp = pd.Timestamp(value)
        timestamp = timestamp.tz_localize('UTC') if timestamp.tzinfo is None else timestamp.tz_convert('UTC')
        condition = compare(ds.field('TIME'), pa.scalar(timestamp, type=time_type))
        expression = condition if expression is None else expression & condition
    names = [name for name in dataset.schema.names if name not in ('TIME', 'month')]
    columns = ['TIME'] + [name for name in (channels or names) if name in names]
    data = dataset.to_table(filter=expression, columns=columns).to_pandas().set_index('TIME').sort_index()
    return data[~data.index.duplicated(keep='last')]
