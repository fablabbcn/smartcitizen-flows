# Forward tasks documentation

The tasks are run with `invoke`. To list the possible tasks:

```
invoke --list
```

To see the help of one task:

```
invoke staplus-mqtt-forward --help
```

Each task available to invoke needs a schema, defined in `public/schemas`. This schema is a `.yaml` file that defines the process to run on both, the input and output. Several options are available and under development.

## SC-STAPLUS Bridge

This task listens to a certain topic on a MQTT Broker following SC format. For instance:

```
bridge/sc/device/sck/<device_token>/readings/raw
```

You can filter the devices based on tags from the SC platform as indicated on the schema.

Then, the task converts both, the topic and payload from the original topic and converts it to a topic and a payload according to the convertion functions defined.

For this to work, you need to set an environment variable for the application in question:

```
SECD_BEARER=...
```

### TODOs

- [ ] Update location descriptions
- [ ] Have vocabularies for both the units of measurement and the measurements themselves
- [ ] Datasheets on original API

### Run

For forwarding data to from SC to STA:

```
invoke staplus-mqtt-forward --schema-file public/schemas/citiobs-schema.yaml -v
```

Make sure you have the schema in the `public/schemas` folder.

## SC-TwinAIR Bridge

Same as above. Except that to run you need to execute:

```
invoke mqtt-forward --schema-file public/schemas/citiobs-schema.yaml -v
```