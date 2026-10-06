"""入库校验规则测试（纯 schema，无需数据库）。"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas import AppConfig, CoalQuality, RunData

from .fixtures import make_coal, make_config, make_run


def _fields(exc: ValidationError) -> set[str]:
    return {str(loc) for e in exc.errors() for loc in e["loc"]}


# --- 煤质 -----------------------------------------------------------------


def test_element_sum_within_tolerance_accepted():
    coal = make_coal(ash_percent=21.7 + 0.4)  # 总和 100.4，偏差 0.4 <= 0.5
    CoalQuality(**coal)


def test_element_sum_beyond_tolerance_rejected():
    coal = make_coal(ash_percent=21.7 + 0.6)  # 总和 100.6，偏差 0.6 > 0.5
    with pytest.raises(ValidationError) as exc:
        CoalQuality(**coal)
    assert exc.value.errors()


def test_nonpositive_qnet_rejected():
    with pytest.raises(ValidationError):
        CoalQuality(**make_coal(qnet_kj_kg=0.0))
    with pytest.raises(ValidationError):
        CoalQuality(**make_coal(qnet_kj_kg=-100.0))


# --- 运行数据 ---------------------------------------------------------------


def test_o2_out_of_range_rejected():
    with pytest.raises(ValidationError):
        RunData(**make_run(o2_percent=22.0))
    with pytest.raises(ValidationError):
        RunData(**make_run(o2_percent=-0.1))
    RunData(**make_run(o2_percent=0.0))
    RunData(**make_run(o2_percent=21.0))


def test_steam_enthalpy_must_exceed_feedwater():
    with pytest.raises(ValidationError):
        RunData(**make_run(steam_enthalpy_kj_kg=1050.0, feedwater_enthalpy_kj_kg=1050.0))
    with pytest.raises(ValidationError):
        RunData(**make_run(steam_enthalpy_kj_kg=1000.0, feedwater_enthalpy_kj_kg=1050.0))


def test_ash_shares_must_sum_to_one():
    with pytest.raises(ValidationError):
        RunData(**make_run(fly_ash_share=0.8, slag_share=0.1))
    RunData(**make_run(fly_ash_share=1.0, slag_share=0.0))


# --- 配置 -------------------------------------------------------------------


def test_nonpositive_uncertainty_rejected():
    config = make_config()
    config["uncertainties"]["relative"]["coal_flow_tph"] = 0.0
    with pytest.raises(ValidationError):
        AppConfig(**config)
    config = make_config()
    config["uncertainties"]["absolute"]["o2_percent"] = -0.3
    with pytest.raises(ValidationError):
        AppConfig(**config)


def test_missing_uncertainty_field_rejected():
    config = make_config()
    del config["uncertainties"]["relative"]["qnet_kj_kg"]
    with pytest.raises(ValidationError):
        AppConfig(**config)


def test_default_config_is_valid():
    AppConfig(**make_config())
