''' Parameters of long processing per sensor type, and per hardware

The baselines of long processing (CO2 with ALS, electrochemical sensors with alphasense_als) need
parameters tuned away from the defaults of the blueprint, and the same values usually work for every
sensor of a type. A parameter set is named after a sensor type and holds kwargs per channel of the
long blueprint, e.g. ASB4_NO2: {"NO2": {"lam": 1e8, "p": 0.01}}:

- Alphasense types (ASB4_NO2, ASA4_CO..., from the first digits of the sensor id) apply to the channel
  whose alphasense_id is a sensor of that type, in each hardware version
- Other types (e.g. SCD30 for NDIR CO2) apply when the blueprint reads a sensor whose name starts
  with the type (SCD30_CO2)

A hardware can override them for its devices (parameters of the hardware: {channel: kwargs}).
Processing (the process blueprint) only takes those of the hardware (process_parameters), as kwargs.
A "lookback_hours" key loads that many hours of readings before the new ones, for channels that need
them (e.g. the baseline of alphasense_als): only the new readings are posted.
Order: blueprint, then parameter sets, then hardware. A "function" key switches the algorithm of the
channel, e.g. {"O3": {"function": "alphasense_803_04"}} keeps the 803 algorithm for the O3 of a
hardware whose NO2 uses the baseline (as chosen per device and gas for TwinAir).
'''
from . import db
from .models import Blueprint, ParameterSet


def alphasense_types():
    ''' {code: type} of the Alphasense sensors, as smartcitizen-connector reads them from sensor ids '''
    from smartcitizen_connector._config import config as connector_config
    return dict(connector_config._as_sensor_codes)


def sensor_types():
    ''' Types a parameter set can be named after '''
    return sorted(set(alphasense_types().values())) + ['SCD30', 'SCD4X']


def channel_type(kwargs, codes):
    alphasense_id = str((kwargs or {}).get('alphasense_id') or '')
    return codes.get(alphasense_id[:3]) if alphasense_id else None


def overrides_for(channel_name, kwargs, sensors, sets, codes):
    ''' Merged kwargs of the parameter sets that apply to a channel, and the names of those sets '''
    merged, used = {}, []
    alphasense = channel_type(kwargs, codes)
    for item in sets:
        if channel_name not in item.channels:
            continue
        if item.name in codes.values():
            applies = alphasense == item.name
        else:
            applies = any(sensor.startswith(item.name) for sensor in sensors)
        if applies:
            merged.update(item.channels[channel_name])
            used.append(item.name)
    return merged, used


LOOKBACK = 'lookback_hours'


def lookback_hours(parameters):
    ''' Hours of readings before the new ones that processing loads: the longest of the channels (0: none) '''
    hours = [kwargs.get(LOOKBACK) for kwargs in (parameters or {}).values() if isinstance(kwargs, dict)]
    return max([value for value in hours if isinstance(value, (int, float)) and value > 0], default=0)


def apply_parameters(device, hardware=None, kind=Blueprint.LONG):
    '''
    Applies the parameter sets and the hardware's parameters to the channels of a device (scdata
    Device after use_blueprint), also in each hardware version. Returns the names of the sets used.
    For processing (kind process), only the hardware's process_parameters: the sets are of long processing
    '''
    long = kind == Blueprint.LONG
    sets = db.session.execute(db.select(ParameterSet)).scalars().all() if long else []
    codes = alphasense_types()
    sensors = list(device.required_sensors)
    hardware_parameters = (getattr(hardware, 'parameters' if long else 'process_parameters', None) or {}) \
        if hardware is not None else {}
    used = set()

    def update(name, kwargs):
        ''' (function or None, kwargs) of a channel '''
        merged, names = overrides_for(name, kwargs, sensors, sets, codes)
        used.update(names)
        merged.update(hardware_parameters.get(name, {}))
        function = merged.pop('function', None)
        # For the run, not for the channel
        merged.pop(LOOKBACK, None)
        return function, dict(kwargs or {}, **merged)

    for channel in device.channels:
        function, channel.kwargs = update(channel.name, channel.kwargs)
        if function:
            channel.function = function
    for version in getattr(device, 'versions', None) or []:
        for channel in version['channels']:
            function, channel['kwargs'] = update(channel['name'], channel.get('kwargs'))
            if function:
                channel['function'] = function
    return sorted(used)
