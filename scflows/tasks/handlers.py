#/usr/bin/python
from importlib import import_module
import os
import yaml
import re
import json
import ast
from scflows.tools import std_out, LazyCallable, find_by_field, parse_sc_json

import traceback
from scflows.config import config
config._out_level = 'DEBUG'

class MappingHandler:
    def __init__(self, module, function, kwargs):

        try:
            hmod = import_module(module)
        except ModuleNotFoundError:
            raise ModuleNotFoundError(f'{module} not found')
        else:
            self.module = module

        lazy_name = f"{self.module}.{function}"
        try:
            function = LazyCallable(lazy_name)
        except ModuleNotFoundError:
            raise ModuleNotFoundError(f'Callable {lazy_name} not available')
        else:
            self.function = function

        self.kwargs = kwargs

    def validate(self):
        return True

    def run(self):
        """
        This function needs to return a valid mapping between
        """

        try:
            result = self.function(**self.kwargs)
        except KeyError:
            raise KeyError('Cant run function')
        else:
            self.result = result

        return self.result

class SchemaHandler:
    def __init__(self, schema_file):
        self.schema_file = schema_file
        self.load_schema()
        # TODO - Make something with it
        self.valid = self.validate()

    def load_schema(self):
        std_out (f"Loading schema: {self.schema_file}")
        if os.path.exists(self.schema_file):
            with open(self.schema_file, 'r') as file:
                self.raw = yaml.safe_load(file)
        else:
            raise FileNotFoundError('Could not find schema')

    def validate(self):
        # Actually do something
        # Validate presence of fields and that they work
        return True

    @property
    def source_topic_std(self):
        source_topic = self.raw['source']['topic']['in']
        return re.sub('<(.*)>', '+', source_topic)

class Message:
    topic: None
    payload: None

class MessageHandler:
    def __init__(self, msg, schema, inbound_mapping, outbound_mapping, payload_mapping = None):
        self.in_msg = msg
        self.out_msg = Message()
        self.schema = schema
        self.inbound_mapping = inbound_mapping
        self.outbound_mapping = outbound_mapping
        self.payload_mapping = payload_mapping

    def convert(self):
        if self.schema.valid:
            # Topic
            self.out_msg.topic = self.convert_topic()
            # Payload
            self.out_msg.payload = self.convert_payload()

        return self.out_msg.topic

    def convert_topic(self):

        if self.inbound_mapping is not None and self.schema.raw['source']['topic'] != 'keep':

            inbound_origin_pty = re.search('<(.*)>', self.schema.raw['source']['topic']['in'])
            # print (f'Origin property: {inbound_origin_pty}')
            inbound_matcher = self.schema.raw['source']['topic']['in'].replace('<', '(?P<').replace('>', '>\w+)')
            # print (f'Matcher: {inbound_matcher}')
            inbound_origin_value = re.match(inbound_matcher, self.in_msg.topic)
            # print (f'Inbound origin value: {inbound_origin_value}')
            # print (f'Inbound origin value group: {inbound_origin_value.group(1)}')
            inbound_target_pty = re.search('<(.*)>', self.schema.raw['source']['topic']['out'])
            # print (f'Target property: {inbound_target_pty}')
            if inbound_target_pty is not None:
                try:
                    inbound_target_value = self.inbound_mapping.result.loc[self.inbound_mapping.result[inbound_origin_pty.group(1)]==inbound_origin_value.group(1)][inbound_target_pty.group(1)].values[0]
                except:
                    inbound_target_value = None
                    pass

                if inbound_target_value is None:
                    inbound_out_topic = None
                else:
                    inbound_out_topic = self.schema.raw['source']['topic']['out'].replace(inbound_target_pty.group(0), str(inbound_target_value))
            else:
                inbound_out_topic = self.schema.raw['source']['topic']['out']

        # TODO - This could be multiple cases
        if inbound_out_topic is not None:
            if self.outbound_mapping is not None and self.schema.raw['destination']['topic'] != 'keep':
                # print (inbound_out_topic)
                outbound_origin_pty = re.search('<(.*)>', self.schema.raw['destination']['topic']['in'])
                # print (f'Origin property: {outbound_origin_pty}')
                outbound_matcher = self.schema.raw['destination']['topic']['in'].replace('<', '(?P<').replace('>', '>\w+)')
                # print (f'Matcher: {outbound_matcher}')
                outbound_origin_value = re.match(outbound_matcher, inbound_out_topic)
                # print (f'Outbound origin value: {outbound_origin_value}')
                # print (f'Outbound origin value group: {outbound_origin_value.group(1)}')
                outbound_target_pty = re.search('<(.*)>', self.schema.raw['destination']['topic']['out'])
                # print (f'Target property: {outbound_target_pty}')
                if outbound_target_pty is not None:
                    try:
                        outbound_target_value = self.outbound_mapping.result.loc[self.outbound_mapping.result[outbound_origin_pty.group(1)]==outbound_origin_value.group(1)][outbound_target_pty.group(1)].values[0]
                    except:
                        outbound_target_value = None

                    if outbound_target_value is None:
                        outbound_out_topic = None
                    else:
                        outbound_out_topic = self.schema.raw['destination']['topic']['out'].replace(outbound_target_pty.group(0), str(outbound_target_value))
                else:
                    outbound_out_topic = self.schema.raw['destination']['topic']['out']
            else:
                outbound_out_topic = inbound_out_topic
        else:
            outbound_out_topic = inbound_out_topic

        # print (f'Target value: {outbound_target_value}')

        return outbound_out_topic

    def convert_payload(self):
        # TODO add other options
        payload_process = self.schema.raw['destination']['payload']['process']
        if payload_process == 'keep':
            return self.in_msg.payload.decode()
        elif payload_process == 'sc-staplus-transform':

            json_payload = parse_sc_json(self.in_msg.payload.decode())

            result = dict()
            observations = list()

            # Find the device
            inbound_origin_pty = re.search('<(.*)>', self.schema.raw['source']['topic']['in'])
            # print (f'Origin property: {inbound_origin_pty}')
            inbound_matcher = self.schema.raw['source']['topic']['in'].replace('<', '(?P<').replace('>', '>\w+)')
            # print (f'Matcher: {inbound_matcher}')
            inbound_origin_value = re.match(inbound_matcher, self.in_msg.topic)
            # print (f'Inbound origin value: {inbound_origin_value}')
            # print (f'Inbound origin value group: {inbound_origin_value.group(1)}')
            inbound_target_pty = 'device_id'
            # print (f'Target property: {inbound_target_pty}')
            if inbound_target_pty is not None:
                try:
                    inbound_target_value = self.inbound_mapping.result.loc[self.inbound_mapping.result[inbound_origin_pty.group(1)]==inbound_origin_value.group(1)][inbound_target_pty].values[0]
                except:
                    inbound_target_value = None
                    pass

            if inbound_target_value is not None:
                device_id = inbound_target_value

                for key, value in json_payload.items():
                    if key == "t": continue
                    # TODO Check why this fails
                    if int(key) in self.payload_mapping['devices'][inbound_target_value]['sensors']:
                        ds_id = self.payload_mapping['devices'][inbound_target_value]['sensors'][int(key)]

                        observation = {
                            "phenomenonTime": json_payload['t'],
                            "resultTime": json_payload['t'],
                            "result": float(value),
                            "Datastream": {"@iot.id": ds_id}
                        }
                        observations.append(observation)

                result ={
                    "creationTime": json_payload['t'],
                    "endTime": json_payload['t'],
                    "name": f"Observations at {json_payload['t']}",
                    "description": "",
                    "Party": {"@iot.id": self.payload_mapping['party']},
                    "Observations": observations
                }

                return json.dumps(result)
