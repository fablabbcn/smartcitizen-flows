import scdata as sc

import sys
import asyncio
import os
import json
import boto3
import awswrangler as wr
import datetime
import botocore

from scflows.config import config
from scflows.custom_logger import logger
from scflows.runlog import log_line
from scflows.tasks.context import describe, device_context, iso, library_warnings
from scflows.tools import refresh_metadata


def backup_location(device_id):
    ''' Where the backups of a device are: parquet files in data/, the last date backed up in request.json '''
    return f"s3://{os.environ.get('S3_DATA_BUCKET', '')}/devices/{device_id}/"


async def dbackup(device, up_to_date=False, context=None):
    ''' See back_up_device. context (a dict) gets what the run used: device, storage, period '''
    task_log = []
    context = {} if context is None else context
    # Why a load or a write failed is in the warnings of scdata and of the Smart Citizen API client
    with library_warnings(task_log):
        try:
            return await back_up_device(device, up_to_date, task_log, context)
        except Exception as error:
            # The run keeps the log up to the error
            logger.exception(f'Backing up device {device} raised')
            task_log.append(log_line('error', f'{type(error).__name__}: {error}'))
            return task_log, ['FAILED', f'{type(error).__name__}: {error}']


async def back_up_device(device, up_to_date, task_log, context):
    '''
        This function makes a backup of a device from SC API into a S3 bucket for later recovery.
        Backups wait for their period (_backup_interval_days) to end, so that each one appends a whole
        period. up_to_date backs up to the last reading without waiting (long processing reads the
        backups and should not lag behind)
    '''

    def logger_handler(msg, level='info'):
        if level == 'info':
            logger.info(msg)
        elif level == 'warning':
            logger.warning(msg)
        elif level == 'error':
            logger.error(msg)
        return log_line(level, msg)

    logger_handler(f'Backup instance for device {device}')

    # Create device from SC API
    # Changes made in flows (calibrations, blueprints) apply without restarting the worker
    refresh_metadata()

    d = sc.Device(blueprint='sc_air', params=sc.APIParams(id=device))
    s3 = boto3.resource('s3')
    task_state = [None, None]

    if d:
        task_log.append(logger_handler(f'Device {device} Initialized'))
        context.update(device_context(d), storage=backup_location(d.id), up_to_date=up_to_date)
        for line in describe(context):
            task_log.append(logger_handler(line))
        task_log.append(logger_handler(f"Backups in {context['storage']}"))
        skip = False
        error = False

        try:
            metadata = s3.Object(f"{os.environ['S3_DATA_BUCKET']}", f"devices/{d.id}/request.json").get()
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == "NoSuchKey":
                # The object does not exist.
                task_log.append(logger_handler('No last date available'))
                # This is a shot in the dark - we don't have a way to know if data is going to be available
                d.options.max_date = d.handler.json.created_at + datetime.timedelta(days=config._backup_interval_days)
                # Not past the last reading either: the next run starts from the date stored after this one
                last_reading = d.handler.json.last_reading_at
                if last_reading is not None and d.options.max_date > last_reading:
                    d.options.max_date = last_reading
                mode = 'overwrite'
            else:
                # Something else has gone wrong.
                task_log.append(logger_handler(f"Cannot read {context['storage']}request.json: {e.response.get('Error')}", 'error'))
                error = True
        else:
            # The object does exist.
            task_log.append(logger_handler('Already requested data...'))
            response = json.loads(metadata['Body'].read().decode('utf-8'))
            last_requested_data = datetime.datetime.fromisoformat(response['last_requested_data'])
            task_log.append(logger_handler(f'Last requested date: {last_requested_data}'))
            context['last_requested_data'] = iso(last_requested_data)
            mode = 'append'

            if last_requested_data < d.handler.json.last_reading_at:

                d.options.min_date = last_requested_data
                d.options.max_date = last_requested_data + datetime.timedelta(days=config._backup_interval_days)

                if d.options.max_date > datetime.datetime.now(tz=datetime.timezone.utc) and not up_to_date:
                    task_log.append(logger_handler(f'Best to wait until period is complete ({d.options.max_date}), skip'))
                    skip = True
                # The next run continues from there (the reading at the boundary is stored twice and
                # dropped when reading)
                elif d.options.max_date > d.handler.json.last_reading_at:
                    d.options.max_date = d.handler.json.last_reading_at

            else:
                skip = True

        if not error:
            if not skip:
                task_log.append(logger_handler(f'Min date: {d.options.min_date}'))
                task_log.append(logger_handler(f'Max date: {d.options.max_date}'))
                context.update(min_date=iso(d.options.min_date), max_date=iso(d.options.max_date), mode=mode)

                if await d.load():
                    task_log.append(logger_handler(f'Device was loaded: {len(d.data)} rows, {len(d.data.columns)} columns ({mode})'))
                    context['rows'] = len(d.data)

                    # Back it up it
                    if d.backup_to_storage(mode=mode):

                        s3object = s3.Object(f"{os.environ['S3_DATA_BUCKET']}", f"devices/{d.id}/request.json")
                        s3object.put(
                            Body=(bytes(json.dumps({"last_requested_data": d.options.max_date.isoformat()}).encode('UTF-8')))
                        )
                        task_state = ['SUCCESS', 'BACKUP_DONE']
                        context['new_last_requested_data'] = iso(d.options.max_date)
                        task_log.append(logger_handler(f'Device was backed-up until {d.options.max_date}'))
                    else:
                        task_log.append(logger_handler('The backup could not be written (see the warnings above)', 'error'))
                        task_state = ['ABORTED', 'BACKUP_FAILED']
                else:
                    task_log.append(logger_handler(f'Device {device} was not loaded', 'warning'))

                    if d.data.empty:
                        task_log.append(logger_handler(f'Device {device} data is empty. Nothing to do', 'warning'))
                        # Even if there is no data, we still store the key
                        s3object = s3.Object(f"{os.environ['S3_DATA_BUCKET']}", f"devices/{d.id}/request.json")
                        s3object.put(
                            Body=(bytes(json.dumps({"last_requested_data": d.options.max_date.isoformat()}).encode('UTF-8')))
                        )
                        task_state = ['ABORTED', 'EMPTY_DATA']
            else:
                task_log.append(logger_handler(f'Device {device} has no new data. Nothing to do', 'warning'))
                task_state = ['ABORTED', 'NO_NEW_DATA']
        else:
            task_log.append(logger_handler(f'Error while connecting to S3 for {device}', 'error'))
            task_state = ['ABORTED', 'UNHANDLED ERROR']

    else:
        task_log.append(logger_handler(f'Device {device} not valid', 'error'))
        task_state = ['ABORTED', 'DEVICE_NOT_VALID']

    task_log.append(logger_handler(f'Concluded job for {device}'))

    return task_log, task_state


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Back up a device of the Smart Citizen API')
    parser.add_argument('--device', type=int, required=True)
    parser.add_argument('--up-to-date', action='store_true', help='Back up to the last reading, without waiting for the period to end')
    args = parser.parse_args()

    log, state = asyncio.run(dbackup(args.device, up_to_date=args.up_to_date))
    logger.info(f'Result: {state}')
