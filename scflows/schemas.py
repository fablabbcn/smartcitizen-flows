''' Validation of metadata sent to the API '''
from datetime import date
from os.path import basename, splitext
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


def url_name(url):
    ''' Name of a file from its url: .../blueprints/sc_air.json -> sc_air '''
    return splitext(basename(urlparse(str(url)).path))[0]


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
    ''' Same structure as the hardware files of smartcitizen-data, plus the blueprint name '''
    model_config = ConfigDict(extra='forbid')

    # Blueprints in flows: one or two of different kinds (process, long, backup). A single
    # blueprint (name) or blueprint_url is accepted too (e.g. from the hardware files)
    blueprints: Optional[List[str]] = None
    blueprint: Optional[str] = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    blueprint_url: Optional[str] = None
    description: Optional[str] = None
    comment: Optional[str] = None
    forwarding: Optional[str] = None
    versions: List[HardwareVersionIn] = []

    @model_validator(mode='after')
    def check_blueprint(self):
        if self.blueprint and self.blueprint_url and url_name(self.blueprint_url) != self.blueprint:
            raise ValueError('"blueprint" and "blueprint_url" refer to different blueprints')
        # The single blueprint served with the list (e.g. a hardware sent back as received) must be in it
        if self.blueprints is not None and self.blueprint_name and self.blueprint_name not in self.blueprints:
            raise ValueError(f'"blueprint" {self.blueprint_name} is not in "blueprints"')
        return self

    @property
    def blueprint_name(self):
        ''' Name of the referenced blueprint, from blueprint or blueprint_url '''
        return self.blueprint or (url_name(self.blueprint_url) if self.blueprint_url else None)

    @property
    def blueprint_names(self):
        ''' The blueprints of the hardware: blueprints, or the single blueprint given '''
        if self.blueprints is not None:
            return list(dict.fromkeys(self.blueprints))
        return [self.blueprint_name] if self.blueprint_name else []


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
    description: str = ''
    unit: str = ''
    # Accepted so that items of the list can be sent back as they are
    name: Optional[str] = None
