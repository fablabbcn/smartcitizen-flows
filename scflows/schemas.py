''' Validation of metadata sent to the API '''
from datetime import date
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Values in calibrations.json are numbers or numeric strings, sometimes empty
CalibrationValue = Union[float, int, str]


class HardwareVersionIn(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    ids: Dict[str, str]
    from_date: Optional[date] = Field(default=None, alias='from')
    to_date: Optional[date] = Field(default=None, alias='to')

    @model_validator(mode='after')
    def check_dates(self):
        if self.from_date and self.to_date and self.from_date >= self.to_date:
            raise ValueError('"from" must be earlier than "to"')
        return self


class HardwareIn(BaseModel):
    ''' A hardware description, as flows serves it: name and url are not stored '''
    model_config = ConfigDict(extra='forbid')

    name: Optional[str] = None
    url: Optional[str] = None
    # Blueprints in flows: one or two of different kinds (process, long, backup)
    blueprints: List[str] = []
    description: Optional[str] = None
    comment: Optional[str] = None
    forwarding: Optional[str] = None
    # Long processing parameters for its devices: {channel: kwargs} (see parameters.py)
    parameters: Optional[Dict[str, Dict[str, Any]]] = None
    versions: List[HardwareVersionIn] = []

    @property
    def blueprint_names(self):
        return list(dict.fromkeys(self.blueprints))


class AlphasenseCalibration(BaseModel):
    ''' Alphasense electrochemical sensor '''
    model_config = ConfigDict(extra='forbid')

    ae_electronic_zero_mv: CalibrationValue
    ae_sensor_zero_mv: CalibrationValue
    pcb_gain_mv_na: CalibrationValue
    we_cross_sensitivity_no2_mv_ppb: CalibrationValue
    we_cross_sensitivity_no2_na_ppb: CalibrationValue
    we_electronic_zero_mv: CalibrationValue
    we_sensitivity_mv_ppb: CalibrationValue
    we_sensitivity_na_ppb: CalibrationValue
    we_sensor_zero_mv: CalibrationValue
    ae_total_zero_mv: Optional[CalibrationValue] = None
    we_total_zero_mv: Optional[CalibrationValue] = None


class AfeCalibration(BaseModel):
    ''' Alphasense AFE board (PT1000) '''
    model_config = ConfigDict(extra='forbid')

    t20: CalibrationValue
    v20: CalibrationValue


def validate_calibration(data):
    ''' Validates calibration data. Returns its kind '''
    from .models import Calibration

    if not isinstance(data, dict):
        raise ValueError('calibration must be an object')
    kind = Calibration.kind_of(data)
    schema = AfeCalibration if kind == Calibration.AFE_BOARD else AlphasenseCalibration
    schema.model_validate(data)
    return kind


class SensorNameIn(BaseModel):
    ''' A sensor name, as in smartcitizen-data/names/SCDevice.json (name given separately) '''
    model_config = ConfigDict(extra='forbid')

    # Smart Citizen API sensor id. 0: no id in the platform
    id: int = Field(ge=0)
    # The sizes of the columns (SensorName)
    description: str = Field('', max_length=255)
    unit: str = Field('', max_length=32)
    # Accepted so that items of the list can be sent back as they are
    name: Optional[str] = None


class ParameterSetIn(BaseModel):
    ''' Parameters of long processing for a sensor type: {channel: kwargs} '''
    model_config = ConfigDict(extra='forbid')

    channels: Dict[str, Dict[str, Any]]
    description: Optional[str] = None
