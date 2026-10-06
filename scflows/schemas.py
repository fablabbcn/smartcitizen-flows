''' Validation of metadata sent to the API '''
from datetime import date
from typing import Dict, List, Optional, Union

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
    ''' Same structure as the hardware files of smartcitizen-data '''
    model_config = ConfigDict(extra='forbid')

    blueprint_url: Optional[str] = None
    description: Optional[str] = None
    comment: Optional[str] = None
    forwarding: Optional[str] = None
    versions: List[HardwareVersionIn] = []


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

    kind = Calibration.kind_of(data)
    schema = AfeCalibration if kind == Calibration.AFE_BOARD else AlphasenseCalibration
    schema.model_validate(data)
    return kind
