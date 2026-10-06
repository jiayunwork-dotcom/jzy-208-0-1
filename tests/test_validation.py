"""领域校验单元测试：不合法数据必须拒收并指出字段。"""
import pytest
from pydantic import ValidationError

from app.config import CalcConfig
from app.schemas import CoalQuality, RunData
from app.validation import (
    DomainValidationError,
    validate_coal_quality,
    validate_run_data,
)

from conftest import SAMPLE_COAL, SAMPLE_RUN


def _run_errors(**overrides):
    with pytest.raises(DomainValidationError) as exc_info:
        validate_run_data(RunData(**{**SAMPLE_RUN, **overrides}))
    return {e["field"] for e in exc_info.value.errors}


def _coal_errors(**overrides):
    with pytest.raises(DomainValidationError) as exc_info:
        validate_coal_quality(CoalQuality(**{**SAMPLE_COAL, **overrides}))
    return {e["field"] for e in exc_info.value.errors}


def test_valid_data_passes():
    validate_run_data(RunData(**SAMPLE_RUN))
    validate_coal_quality(CoalQuality(**SAMPLE_COAL))


def test_element_sum_deviation_rejected():
    # 碳加 1.0，元素和 = 101.0，偏差 1.0 > 0.5
    assert "elemental_sum" in _coal_errors(carbon=SAMPLE_COAL["carbon"] + 1.0)


def test_element_sum_within_tolerance_accepted():
    validate_coal_quality(CoalQuality(**{**SAMPLE_COAL, "carbon": SAMPLE_COAL["carbon"] + 0.4}))


def test_non_positive_qnet_rejected():
    assert "qnet" in _coal_errors(qnet=0.0)
    assert "qnet" in _coal_errors(qnet=-100.0)


def test_flue_o2_out_of_range_rejected():
    assert "flue_o2" in _run_errors(flue_o2=-0.1)
    assert "flue_o2" in _run_errors(flue_o2=21.1)


def test_main_steam_enthalpy_not_above_feedwater_rejected():
    assert "main_steam_enthalpy" in _run_errors(
        main_steam_enthalpy=SAMPLE_RUN["feedwater_enthalpy"]
    )
    assert "main_steam_enthalpy" in _run_errors(
        main_steam_enthalpy=SAMPLE_RUN["feedwater_enthalpy"] - 10.0
    )


def test_ash_shares_not_summing_to_one_rejected():
    assert "fly_ash_share" in _run_errors(fly_ash_share=0.8, slag_share=0.1)


def test_non_positive_uncertainty_rejected():
    with pytest.raises(ValidationError):
        CalcConfig(uncertainties={"coal_flow": {"relative_pct": 0.0}})
    with pytest.raises(ValidationError):
        CalcConfig(uncertainties={"coal_flow": {"absolute": -1.0}})


def test_unknown_uncertainty_key_rejected():
    with pytest.raises(ValidationError):
        CalcConfig(uncertainties={"no_such_measurement": {"relative_pct": 1.0}})
