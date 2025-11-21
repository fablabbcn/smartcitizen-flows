import scdata as sc

import sys
import asyncio
import os
import json
import boto3
import awswrangler as wr
import datetime
import botocore

from scflows.worker import app
from scflows.config import config
from scflows.custom_logger import logger
from celery.result import AsyncResult
from celery.exceptions import Ignore
from celery import states

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

    logger_handler(f'Processing instance for device {device}')

    # Create device from SC API
    d = sc.Device(params=sc.APIParams(id=device))
    s3 = boto3.resource('s3')
    task_state = [None, None]

    if d:
        task_log.append(logger_handler(f'Device {device} Initialized'))
        skip = False

        try:
            metadata = s3.Object(f"{os.environ['S3_DATA_BUCKET']}", f"devices/{d.id}/request.json").get()
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == "NoSuchKey":
                # The object does not exist.
                task_log.append(logger_handler('No last date available'))
                d.options.max_date = d.handler.json.created_at + datetime.timedelta(days=config._backup_interval_days)
                mode = 'overwrite'
            else:
                # Something else has gone wrong.
                logger_handler(e.response)
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

        if not skip:
            task_log.append(logger_handler(f'Min date: {d.options.min_date}'))
            task_log.append(logger_handler(f'Max date: {d.options.max_date}'))

            if await d.load():
                task_log.append(logger_handler(f'Device was loaded: {d.loaded}'))

                # Back it up it
                if d.backup(mode=mode):

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
                    task_state = ['ABORTED', 'EMPTY_DATA']
        else:
            task_log.append(logger_handler(f'Device {device} has no new data. Nothing to do', 'warning'))
            task_state = ['ABORTED', 'NO_NEW_DATA']

    else:
        task_log.append(logger_handler(f'Device {device} not valid', 'error'))
        task_state = ['ABORTED', 'DEVICE_NOT_VALID']

    task_log.append(logger_handler(f'Concluded job for {device}'))

    return task_log, task_state

@app.task(bind=True, track_started=True, name='scflows.tasks.dbackup_task')
def dbackup_task(self, device):
    result, state = asyncio.run(dbackup(device))
    logger.info('dbackup')
    logger.info(result)
    logger.info(state)

    # Raise custom state
    if state[0] != 'SUCCESS':

        self.update_state(
            state=state[0],
            meta={'message': state[1]})
        with self.app.events.default_dispatcher() as dispatcher:
            dispatcher.send('task-custom_state', field1='value1', field2='value2')

        raise Ignore()
    return result

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv:
        print('dbackup: Backup device of SC API into custom storage')
        print('USAGE:\n\rdbackup.py [options]')
        print('options:')
        print('--device <device-number>: device to backup')
        # TODO Add custom storage
        # print('--dest <type>: backup destination')
        print('--celery: task execution is managed via celery worker')
        sys.exit()

    loop = asyncio.get_event_loop()

    if '--device' in sys.argv:
        device = int(sys.argv[sys.argv.index('--device')+1])
    else:
        logger.error('Missing device')
        sys.exit()

    logger.info(f'Backing up device: {device}')

    if '--celery' in sys.argv:
        logger.info(f'Using celery backend...')
        task_id = dbackup_task.s().delay(device = device)
        logger.info(f'Task ID: {task_id}')

        # Wait for result
        result = AsyncResult(task_id, app=app)
        result.wait(timeout=60)

        logger.info('Task result:')
        for res in result.get():
            logger.info(res)
    else:
        loop.run_until_complete(dbackup(device))

    loop.close()


