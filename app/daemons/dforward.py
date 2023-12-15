#/usr/bin/python
from smartcitizen_connector import search_by_query
import paho.mqtt.client as mqtt
from tools import *
import os
import sys
import yaml
import re

# TODO - Remove
devices_hack = {
    16865: "ebnawm",
    16866: "a1ab97",
    16869: "d7f78b",
    16870: "ea70ac",
    16867: "89a779",
    16868: "491877"
}

def get_devices(tag):
    d = search_by_query(endpoint='devices',key='tags_name', search_matcher='eq', value=tag)
    return d

def load_schema(schema_file):
    if os.path.exists(schema_file):
        with open(schema_file, 'r') as file:
            schema = yaml.safe_load(file)
        return schema
    else:
        return None

def oneLine(msg):
    enablePrint()
    sys.stdout.write(msg)
    sys.stdout.flush()
    if not verbose: blockPrint()

def blockPrint():
    sys.stdout = open(os.devnull, 'w')

def enablePrint():
    sys.stdout = sys.__stdout__

# Verbose
verbose = False
blockPrint()
if '-v' in sys.argv:
    verbose = True
    enablePrint()

load_env('.env')

class Message:
    topic: None
    payload: None

class MessageHandler:
    def __init__(self, msg):
        self.in_msg = msg
        self.out_msg = Message()

    def convert(self, schema):
        if self.check_schema(schema):
            # Topic
            self.out_msg.topic = self.convert_topic(self.in_msg.topic, schema['topic'])
            # Payload
            self.out_msg.payload = self.convert_payload(self.in_msg.payload, schema['payload'])

        return self.out_msg

    def convert_topic(self, topic, topic_map):
        origin_pty = re.search('<(.*)>', topic_map['origin'])
        # print (f'Origin property: {origin_pty}')
        matcher = topic_map['origin'].replace('<', '(?P<').replace('>', '>\w+)')
        # print (matcher)
        origin_value = re.match(matcher, topic)
        target_pty = re.search('<(.*)>', topic_map['target'])
        # print (f'Target property: {target_pty}')

        try:
            target_value = devices.loc[devices[origin_pty.group(1)]==origin_value.group(1)][target_pty.group(1)].values[0]
        except:
            target_value = None
            pass

        if target_value is None: return None
        return topic_map['target'].replace(target_pty.group(0), str(target_value))

    def convert_payload(self, payload, payload_map):
        # TODO add other options
        if payload_map == 'keep':
            return payload

    def check_schema(self, schema):
        # TODO add real checks
        return True

def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))
    client.subscribe("bridge/sc/device/sck/+/readings/raw")

def on_message(client, userdata, msg):

    m = MessageHandler(msg)
    msg = m.convert(schema)

    if m.out_msg.topic is not None:
        print ('Original msg')
        print('\t' + msg.topic+" "+str(msg.payload))
        print ('Destination msg')
        print('\t' + str(m.out_msg.topic)+" "+str(m.out_msg.payload))

        # dclient.publish(m.out_msg.topic, payload=str(m.out_msg.payload))

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv or len(sys.argv) < 2 or (not '--tag' in sys.argv and not '--schema' in sys.argv):
        print('USAGE:\n\n\tpython dforward.py [options] action[s] --tag [tag] --schema [schema-file]')
        print('\nOptions:')
        print('\t-v: verbose')
        print('\t--dry-run: dry run (no forwarding)')
        print('\nActions:\n\tstart: \n\t -- \n\t--')
        print('\t--tag: tag to forward')
        print('\t--schema: file pointing to schema file')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    if '--tag' in sys.argv:
        tag = sys.argv[sys.argv.index('--tag')+1]
    else:
        raise ValueError('Need to specify a tag')

    if '--schema' in sys.argv:
        schema_file = sys.argv[sys.argv.index('--schema')+1]
    else:
        raise ValueError('Need to specify a schema file')

    # Source client
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    print (f"Source broker: {environ['BROKER']}:{environ['PORT']}")
    print (f"\tSource user: {environ['USER']}")
    client.username_pw_set(environ['USER'], password=None)
    # TODO - Enable SSL
    client.connect(environ['BROKER'], int(environ['PORT']), 60)
    schema = load_schema(schema_file)
    # TODO - Return the token for admin queries
    # Get devices from that tag
    devices = get_devices(tag)
    devices['id'] = devices.index
    devices['token'] = devices['id'].map(devices_hack)

    # Destination client
    # dclient = mqtt.Client()
    # dclient.on_connect = on_connect
    # print (f"Source broker: {schema['destination']['broker']}:{int(schema['destination']['port'])}")
    # print (f"\tDestination user: {schema['destination']['credentials']['username']}")
    # dclient.username_pw_set(username = schema['destination']['credentials']['username'], \
    #     password = schema['destination']['credentials']['password']
    # )
    # dclient.connect(schema['destination']['broker'], int(schema['destination']['port']), 60)

    if not dry_run:
        print ('Looping')
        client.loop_forever()
