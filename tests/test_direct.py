"""正平衡单元测试（无需数据库）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.direct import direct_efficiency

from .fixtures import make_coal, make_config, make_run


def test_direct_efficiency_hand_computed():
    run = make_run()
    coal = make_coal()
    config = make_config()
    result = direct_efficiency(run, coal, config)
    expected_output = (
        1000.0 * (3400.0 - 1050.0)
        + 850.0 * (3550.0 - 3050.0)
        - 5.0 * (config["blowdown_water_enthalpy_kj_kg"] - 1050.0)
    )
    expected_input = 142.0 * 21000.0
    assert result["eta_percent"] == pytest.approx(100.0 * expected_output / expected_input)
    assert result["input_heat_kj_h"] == pytest.approx(expected_input)


def test_more_coal_lowers_direct_efficiency():
    base = direct_efficiency(make_run(), make_coal(), make_config())
    more_coal = direct_efficiency(make_run(coal_flow_tph=160.0), make_coal(), make_config())
    assert more_coal["eta_percent"] < base["eta_percent"]


def test_blowdown_reduces_output():
    no_blowdown = direct_efficiency(make_run(blowdown_flow_tph=0.0), make_coal(), make_config())
    with_blowdown = direct_efficiency(make_run(), make_coal(), make_config())
    assert with_blowdown["eta_percent"] < no_blowdown["eta_percent"]
