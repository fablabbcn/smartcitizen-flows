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
from scflows.tools import refresh_metadata

async def dbackup(device):
    '''
        This function makes a backup of a device from SC API into a S3 bucket for later recovery
    '''
    task_log = []

    def logger_handler(msg, level='info'):
        if level == 'info':
            logger.info(msg)
        elif level == 'warning':
            logger.warning(msg)
        elif level == 'error':
            logger.error(msg)
        return f'{level}: {msg}'

    logger_handler(f'Backup instance for device {device}')

    # Create device from SC API
    # Changes made in flows (calibrations, blueprints) apply without restarting the worker
    refresh_metadata()

    d = sc.Device(blueprint='sc_air', params=sc.APIParams(id=device))
    s3 = boto3.resource('s3')
    task_state = [None, None]

    if d:
        task_log.append(logger_handler(f'Device {device} Initialized'))
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
                mode = 'overwrite'
            else:
                # Something else has gone wrong.
                logger_handler(e.response, 'error')
                error = True
        except botocore.exceptions.ClientError as e:
            # Something else has gone wrong.
            logger_handler(e.response, 'error')
            error = True
        else:
            # The object does exist.
            task_log.append(logger_handler('Already requested data...'))
            response = json.loads(metadata['Body'].read().decode('utf-8'))
            last_requested_data = datetime.datetime.fromisoformat(response['last_requested_data'])
            task_log.append(logger_handler(f'Last requested date: {last_requested_data}'))
            mode = 'append'

            if last_requested_data < d.handler.json.last_reading_at:

                d.options.min_date = last_requested_data
                d.options.max_date = last_requested_data + datetime.timedelta(days=config._backup_interval_days)

                if d.options.max_date > datetime.datetime.now(tz=datetime.timezone.utc):
                    task_log.append(logger_handler(f'Best to wait until period is complete, skip'))
                    skip = True
                elif d.options.max_date > d.handler.json.last_reading_at:
                    d.options.max_date = d.handler.json.last_reading_at

            else:
                skip = True

        if not error:
            if not skip:
                task_log.append(logger_handler(f'Min date: {d.options.min_date}'))
                task_log.append(logger_handler(f'Max date: {d.options.max_date}'))

                if await d.load():
                    task_log.append(logger_handler(f'Device was loaded: {d.loaded}'))

                    # Back it up it
                    if d.backup_to_storage(mode=mode):

                        s3object = s3.Object(f"{os.environ['S3_DATA_BUCKET']}", f"devices/{d.id}/request.json")
                        s3object.put(
                            Body=(bytes(json.dumps({"last_requested_data": d.options.max_date.isoformat()}).encode('UTF-8')))
                        )
                        task_state = ['SUCCESS', 'BACKUP_DONE']
                        task_log.append(logger_handler(f'Device was backed-up'))
                    else:
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
    args = parser.parse_args()

    log, state = asyncio.run(dbackup(args.device))
    logger.info(f'Result: {state}')
