import secd_staplus_client as staplus
from secd_staplus_client.service import auth_handler
from geojson import Point

from smartcitizen_connector.sensor import get_sensors
from smartcitizen_connector.measurement import get_measurements

from invoke import task
from requests import post
import os
import random
import paho.mqtt.client as mqtt

from scflows.tasks.handlers import SchemaHandler, MappingHandler, MessageHandler
from scflows.tools import std_out, load_env, find_by_field
from scflows.config import config
config._out_level = 'DEBUG'

@task(help=\
    {'verbose': "verbose",
     'dry_run': "dry run (no forwarding)",
     'schema_file': "file pointing to schema file",
     'create_sensors': "create sensors on staplus endpoint"
})
def staplus_mqtt_forward(c, verbose=False, dry_run=False, schema_file=None, create_sensors=False):

    def staplus_create_sensors():
        # TODO make this so that this doesn't create a sensor should there be already one with
        # the same properties and no datastream (expand on datastream is none)
        std_out('Getting sensors from SC API')
        sc_sensors = get_sensors()
        std_out('Done')

        for sensor in sc_sensors:
            std_out(f'Creating sensor: {sensor.id}')
            sta_sensor = staplus.Sensor(
                name=sensor.name,
                description=sensor.description,
                encoding_type='text/html',
                properties={'smartcitizen_sensor_id': sensor.id},
                # TODO Sensor Metadata
                metadata='https://docs.smartcitizen.me/'
            )

            r = service.create(sta_sensor)
        std_out('Done')

    def staplus_create_or_get_party(model=True):
        """
        Create or get a party from staplus
        """
        party = staplus.Party(description='',
            display_name=schema.raw['destination']['server']['credentials']['username'],
            role='individual')

        party_id = service.create(party)
        std_out(f'Party ID: {party_id}')

        if model: return service.parties().find(party_id)
        return party_id

    def staplus_create_or_get_thing(device=None, party = None):
        if device is None:
            raise ValueError('Device is None')

        # Check thing
        sta_location = staplus.Location(name=device.loc['location']['city'],
            # TODO - What to put in location.description
            description="A beautiful, beautiful place",
            location=Point((device.loc['location']['latitude'],
                device.loc['location']['longitude'])),
            encoding_type='application/geo+json')

        result = service.parties().query().filter("authId eq '" + sta_party.auth_id + "'").expand('Things').list()
        thing_found = False
        thing = None

        if result.entities[0].things.entities:
            for _thing in result.entities[0]._things.entities:
                if 'sc_device_id' not in _thing.properties: continue
                if _thing.properties['sc_device_id'] == str(device.name):
                    std_out (f'Thing exists. Thing: {_thing.name}. SC Device: {device.name}')
                    thing_found = True
                    # Set the location of the thing to avoid having to send location each time
                    sta_location.things = [_thing]
                    locationId = service.create(sta_location)
                    thing = _thing
                    break

        if thing_found == False:
            std_out(f'Thing does not exist for: {device.name}. Creating')
            std_out(f"Name: {device.loc['name']}. Description: {device.loc['description']}. Device ID: {device.name}")

            # Hack for empty descriptions
            if device.loc['description'] == '':
                description = device.loc['name']
            else:
                description = device.loc['description']

            new_thing = staplus.Thing(
                name=device.loc['name'],
                description=description,
                properties={'sc_device_id': str(device.name)})
            new_thing.locations = [sta_location]
            new_thing.party = sta_party
            new_thingID = service.create(new_thing)
            std_out(f'Thing created. Thing ID: {new_thingID}')
            thing = new_thing

            # thing = service.things().find(new_thingID)

        return thing

    def sta_plus_create_or_get_devices():
        # Result dict
        devices_result = dict()

        for sc_device_id in inbound_mapping.result.index:

            sc_device = inbound_mapping.result.loc[sc_device_id,:]
            sta_thing = staplus_create_or_get_thing(device=sc_device, party= sta_party)

            ds = service.datastreams().query().filter("Thing/id eq '" + str(sta_thing.id) + "'").expand("ObservedProperty").list()
            data_result = {'thing_id': str(sta_thing.id), 'sensors': dict()}

            for sc_sensor in sc_device.data['sensors']:
                print (sc_sensor)
                sc_measurement_id = sc_sensor['measurement']['id']
                sc_measurement = find_by_field(sc_measurements, sc_measurement_id, 'id')

                sta_observed_property = staplus.ObservedProperty(sc_measurement.name,
                    # TODO Vocabulary for measurements
                    f'http://fake-vocab.measurement/{sc_measurement.name}',
                    sc_measurement.description)

                observed_pty_id = None

                # See if we can reuse entities
                if ds.entities:
                    for ds_entity in ds.entities:
                        if ds_entity.observed_property.definition == sta_observed_property.definition:
                            observed_pty_id = ds_entity.id
                            std_out(f'Found observed property in datastream: {ds_entity.id}. Definition: {sta_observed_property.definition}')
                            break

                if observed_pty_id is not None:
                    ds_ID = observed_pty_id
                else:
                    # Set units
                    sta_units = staplus.UnitOfMeasurement(
                        # TODO Vocabulary for units
                        name=sc_sensor['unit'],
                        symbol=sc_sensor['unit'],
                        definition=f"https://fake-vocab.org/vocab/unit/{sc_sensor['unit']}")

                    # Make Datastream
                    sta_datastream = staplus.Datastream(
                        name=sc_measurement.name,
                        description=f'{sc_measurement.name} measured with {sta_thing.name}',
                        # TODO What is this?
                        observation_type='http://www.opengis.net/def/observationType/OGC-OM/2.0/OM_Measurement',
                        unit_of_measurement=sta_units)

                    sta_sensor = staplus.Sensor(
                        name=sc_sensor['name'],
                        description=sc_sensor['description'],
                        encoding_type='text/html',
                        properties={'smartcitizen_sensor_id': sc_sensor['id']},
                        # TODO sensor
                        metadata='https://docs.smartcitizen.me/'
                    )

                    sta_datastream.observed_property = sta_observed_property
                    sta_datastream.party = sta_party
                    sta_datastream.thing = sta_thing
                    sta_datastream.license = sta_license
                    sta_datastream.sensor = sta_sensor

                    if sta_thing.datastreams is not None:
                        for thing_ds in sta_thing.datastreams:
                            if 'sc_sensor_id' in thing_ds.sensor.properties.keys() and thing_ds.sensor.properties['sc_sensor_id'] == sensor['id']:
                                sta_datastream.sensor = service.sensors.find(thing_ds.sensor.id)
                                break

                    ds_ID = service.create(sta_datastream)
                    # TODO - Why getting the thing again?
                    # thing = service.things().query().filter("id eq '" + str(thing.id) + "'").expand('Datastreams/Sensor').list().get(0)

                data_result['sensors'][sc_sensor['id']] = ds_ID

            devices_result[sc_device.name] = data_result

        return devices_result

    def on_connect_source(client, userdata, flags, rc):

        client.subscribe(schema.source_topic_std)
        std_out(f"Connected to source with result code {rc}", "SUCCESS")

    def on_connect_destination(client, userdata, flags, rc):

        std_out(f"Connected to destination with result code {rc}", "SUCCESS")

    def on_message(client, userdata, msg):

        m = MessageHandler(msg, schema, inbound_mapping, outbound_mapping, payload_map)
        if m.convert() is not None:
            if m.out_msg.payload is not None:
                # std_out(f'Original msg: {str(msg.topic)} {str(msg.payload.decode())}')
                # std_out(f'Destination msg: {str(m.out_msg.topic)} {str(m.out_msg.payload)}')
                dclient.publish(m.out_msg.topic, payload=m.out_msg.payload)

    def service_authorize():
        headers = {'Authorization':'Basic ' + os.environ['SECD_BEARER'],
            'Content-type': 'application/x-www-form-urlencoded',
            'accept': 'application/json'}

        r = post('https://www.authenix.eu/oauth/token', headers=headers, data = "grant_type=client_credentials&scope=citiobs.secd.eu#create openid")

        return r.json()

    # TODO - Remove
    load_env('/home/oscar/Documents/projects/fablab/smartcitizen/repositories/data/smartcitizen-flows/.env')

    if schema_file is None:
        raise ValueError('Need to specify a schema file')

    schema = SchemaHandler(schema_file)

    # TODO On disconnect, this should create a new token
    if schema.raw['destination']['broker']['credentials']['password'] == '<on-demand>':
        response = service_authorize()
        destination_token = response['access_token']
    else:
        destination_token = schema.raw['destination']['broker']['credentials']['password']

    url = schema.raw['destination']['server']['url']
    auth = auth_handler.AuthHandler(destination_token)
    service = staplus.STAplusService(url, auth_handler=auth)

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
        if schema.raw['source']['mapping'] == 'reuse': outbound_mapping = None
        else:
            outbound_mapping = MappingHandler(
                schema.raw['source']['mapping']['module'],
                schema.raw['source']['mapping']['handler'],
                schema.raw['source']['mapping']['kwargs'])

        if outbound_mapping is not None:
            outbound_mapping.run()

            # TODO - Temporary hack to make this dynamic until connector returns this
            if schema.raw['source']['mapping']['module'] == 'smartcitizen_connector':
                outbound_mapping.result['device_id'] = outbound_mapping.result.index

    # TODO put it in a mapping
    # Create sensors
    if create_sensors: staplus_create_sensors()
    # Create party
    sta_party = staplus_create_or_get_party(model=True)
    # CC-BY license for all datastreams
    sta_license = service.licenses().find('CC_BY')
    # Get measurements
    sc_measurements = get_measurements()
    # Map SC to STAPLUS
    devices_map = sta_plus_create_or_get_devices()
    payload_map = {'party': sta_party.id, 'devices': devices_map}
    print (payload_map)

    # Source client
    client = mqtt.Client()
    client.on_connect = on_connect_source
    client.on_message = on_message
    std_out (f"Source broker: {schema.raw['source']['broker']['url']}:{schema.raw['source']['broker']['port']}")
    std_out (f"Source user: {schema.raw['source']['broker']['credentials']}")
    client.username_pw_set(schema.raw['source']['broker']['credentials']['username'],
        password=schema.raw['source']['broker']['credentials']['password'])
    # TODO - Enable SSL (via .env)
    std_out(f'Connecting to source broker...')
    client.connect(schema.raw['source']['broker']['url'], int(schema.raw['source']['broker']['port']), 60)

    # Destination client
    dclient = mqtt.Client(f'python-mqtt-{random.randint(0, 1000)}')
    dclient.on_connect = on_connect_destination
    std_out (f"Destination broker: {schema.raw['destination']['broker']['url']}:{int(schema.raw['destination']['broker']['port'])}")
    std_out (f"\tDestination: {schema.raw['destination']['broker']['credentials']}, Token: {destination_token}")
    dclient.username_pw_set(username = schema.raw['destination']['broker']['credentials']['username'], password = destination_token)
    std_out(f'Connecting to destination broker...')
    dclient.connect(schema.raw['destination']['broker']['url'], int(schema.raw['destination']['broker']['port']), 60)

    if not dry_run:
        std_out('Looping...')
        client.loop_forever()

