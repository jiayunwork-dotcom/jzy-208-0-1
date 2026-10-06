"""正平衡单元测试。"""
import pytest

from app import direct
from app.config import CalcConfig
from app.schemas import CoalQuality, RunData

from conftest import SAMPLE_COAL, SAMPLE_RUN


def test_direct_efficiency_heat_balance():
    run = RunData(**SAMPLE_RUN)
    coal = CoalQuality(**SAMPLE_COAL)
    config = CalcConfig()
    result = direct.compute(run, coal, config)

    expected_output = (
        SAMPLE_RUN["main_steam_flow"]
        * (SAMPLE_RUN["main_steam_enthalpy"] - SAMPLE_RUN["feedwater_enthalpy"])
        + SAMPLE_RUN["rh_steam_flow"]
        * (SAMPLE_RUN["rh_outlet_enthalpy"] - SAMPLE_RUN["rh_inlet_enthalpy"])
        + SAMPLE_RUN["blowdown_flow"]
        * (config.blowdown_water_enthalpy - SAMPLE_RUN["feedwater_enthalpy"])
    )
    expected_input = SAMPLE_RUN["coal_flow"] * SAMPLE_COAL["qnet"]

    assert result.output_heat == pytest.approx(expected_output)
    assert result.input_heat == pytest.approx(expected_input)
    assert result.efficiency == pytest.approx(100.0 * expected_output / expected_input)


def test_direct_efficiency_inverse_with_coal_flow():
    """煤量加倍而产出不变，正平衡效率减半。"""
    config = CalcConfig()
    coal = CoalQuality(**SAMPLE_COAL)
    base = direct.compute(RunData(**SAMPLE_RUN), coal, config)
    doubled = direct.compute(
        RunData(**{**SAMPLE_RUN, "coal_flow": SAMPLE_RUN["coal_flow"] * 2}), coal, config
    )
    assert doubled.efficiency == pytest.approx(base.efficiency / 2)
