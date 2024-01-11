#/usr/bin/python
import paho.mqtt.client as mqtt
from importlib import import_module
import os
import sys
import yaml
import re
from scflows.tools import std_out, load_env, LazyCallable
from scflows.tasks.handlers import MessageHandler, SchemaHandler, Message, MappingHandler
from invoke import task

import traceback
from scflows.config import config
config._out_level = 'DEBUG'

def blockPrint():
    sys.stdout = open(os.devnull, 'w')

def enablePrint():
    sys.stdout = sys.__stdout__

@task(help=\
    {'verbose': "verbose",
     'dry_run': "dry run (no forwarding)",
     'schema_file': "file pointing to schema file"
})
def mqtt_forward(c, verbose=False, dry_run=False, schema_file=None):
    """
    Forwards data from one MQTT-stream to another
    """

    def on_connect_source(client, userdata, flags, rc):

        client.subscribe(schema.source_topic_std)
        std_out(f"Connected to source with result code {rc}", "SUCCESS")

    def on_connect_destination(client, userdata, flags, rc):

        std_out(f"Connected destination to with result code {rc}", "SUCCESS")

    def on_message(client, userdata, msg):

        m = MessageHandler(msg, schema, inbound_mapping, outbound_mapping)
        if m.convert() is not None:
            std_out(f'Original msg: {str(msg.topic)} {str(msg.payload.decode())}')
            std_out(f'Destination msg: {str(m.out_msg.topic)} {str(m.out_msg.payload)}')
            # TODO - These could be multiple messages
            # m.out_msg could be a list of tuples
            # dclient.publish(m.out_msg.topic, payload=str(m.out_msg.payload))

    if schema_file is None:
        raise ValueError('Need to specify a schema file')

    schema = SchemaHandler(schema_file)
    if not schema.valid:
        raise ValueError ("Issue with schema!")

    if 'mapping' in schema.raw['source']:
        if schema.raw['source']['mapping'] == 'reuse': inbound_mapping = None
        else:
            inbound_mapping = MappingHandler(
                schema.raw['source']['mapping']['module'],
                schema.raw['source']['mapping']['handler'],
                schema.raw['source']['mapping']['kwargs'])

        if inbound_mapping is not None:
            inbound_mapping.run()

            # TODO - Temporary hack to make this dynamic until connector returns this
            if schema.raw['source']['mapping']['module'] == 'smartcitizen_connector':
                inbound_mapping.result['device_id'] = inbound_mapping.result.index

    if 'mapping' in schema.raw['destination']:
        if schema.raw['destination']['mapping'] == 'reuse': outbound_mapping = None
        else:
            outbound_mapping = MappingHandler(
                schema.raw['destination']['mapping']['module'],
                schema.raw['destination']['mapping']['handler'],
                schema.raw['destination']['mapping']['kwargs'])

        if outbound_mapping is not None:
            outbound_mapping.run()

            # TODO - Temporary hack to make this dynamic until connector returns this
            if schema.raw['destination']['mapping']['module'] == 'smartcitizen_connector':
                outbound_mapping.result['device_id'] = outbound_mapping.result.index

    # Source client
    client = mqtt.Client()
    client.on_connect = on_connect_source
    client.on_message = on_message
    std_out (f"Source broker: {schema.raw['source']['broker']}:{schema.raw['source']['port']}")
    std_out (f"Source user: {schema.raw['source']['credentials']}")
    client.username_pw_set(schema.raw['source']['credentials']['username'], password=schema.raw['source']['credentials']['password'])
    # TODO - Enable SSL (via .env)
    std_out(f'Connecting to source broker...')
    client.connect(schema.raw['source']['broker'], int(schema.raw['source']['port']), 60)

    # Destination client
    dclient = mqtt.Client()
    dclient.on_connect = on_connect_destination
    std_out (f"Destination broker: {schema.raw['destination']['broker']}:{int(schema.raw['destination']['port'])}")
    std_out (f"\tDestination user: {schema.raw['destination']['credentials']}")
    dclient.username_pw_set(username = schema.raw['destination']['credentials']['username'], password = schema.raw['destination']['credentials']['password']
    )
    std_out(f'Connecting to destination broker...')
    dclient.connect(schema.raw['destination']['broker'], int(schema.raw['destination']['port']), 60)

    if not dry_run:
        std_out('Looping...')
        client.loop_forever()
