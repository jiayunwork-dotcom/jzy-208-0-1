"""反平衡各项损失单元测试（无需数据库）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.indirect import CARBON_COMBUSTION_HEAT, heat_losses

from .fixtures import make_coal, make_config, make_run


def _losses(run=None, coal=None, config=None):
    return heat_losses(run or make_run(), coal or make_coal(), config or make_config())


def test_flue_gas_temp_trend():
    """其他条件不变，排烟温度升高时排烟损失增大、效率降低。"""
    base = _losses()
    hotter = _losses(run=make_run(flue_gas_temp_c=165.0))
    assert hotter["losses"]["q2_flue_gas_percent"] > base["losses"]["q2_flue_gas_percent"]
    assert hotter["eta_percent"] < base["eta_percent"]


def test_zero_fly_ash_carbon_leaves_only_slag_q4():
    """飞灰含碳为零时，机械不完全燃烧损失只剩炉渣那部分。"""
    result = _losses(run=make_run(fly_ash_carbon_percent=0.0))
    coal = make_coal()
    run = make_run(fly_ash_carbon_percent=0.0)
    # 手工核算炉渣部分的未燃尽碳
    unburnt = (coal["ash_percent"] / 100.0) * (
        run["slag_share"] * run["slag_carbon_percent"] / (100.0 - run["slag_carbon_percent"])
    )
    expected_q4 = 100.0 * CARBON_COMBUSTION_HEAT * unburnt / coal["qnet_kj_kg"]
    assert result["losses"]["q4_mechanical_incomplete_percent"] == pytest.approx(expected_q4)
    assert result["losses"]["q4_mechanical_incomplete_percent"] > 0.0


def test_q4_zero_when_all_ash_carbon_zero():
    result = _losses(run=make_run(fly_ash_carbon_percent=0.0, slag_carbon_percent=0.0))
    assert result["losses"]["q4_mechanical_incomplete_percent"] == pytest.approx(0.0)


def test_q3_increases_with_co():
    low = _losses(run=make_run(co_percent=0.0))
    high = _losses(run=make_run(co_percent=0.5))
    assert low["losses"]["q3_chemical_incomplete_percent"] == pytest.approx(0.0)
    assert high["losses"]["q3_chemical_incomplete_percent"] > 0.0


def test_eta_equals_100_minus_losses():
    result = _losses()
    total = sum(result["losses"].values())
    assert result["eta_percent"] == pytest.approx(100.0 - total)


def test_q5_fixed_vs_load_scaled():
    fixed = _losses(config=make_config(q5_mode="fixed", q5_fixed_percent=0.6))
    assert fixed["losses"]["q5_radiation_percent"] == pytest.approx(0.6)
    # 实际负荷为额定的一半时，散热损失按比例放大一倍
    scaled = _losses(
        run=make_run(steam_flow_tph=500.0),
        config=make_config(q5_mode="load_scaled", q5_rated_percent=0.5,
                           rated_steam_flow_tph=1000.0),
    )
    assert scaled["losses"]["q5_radiation_percent"] == pytest.approx(1.0)


def test_excess_air_flows_into_q2():
    """氧量越高，过量空气越大，排烟损失越大。"""
    low_o2 = _losses(run=make_run(o2_percent=2.0))
    high_o2 = _losses(run=make_run(o2_percent=8.0))
    assert high_o2["combustion"]["alpha"] > low_o2["combustion"]["alpha"]
    assert high_o2["losses"]["q2_flue_gas_percent"] > low_o2["losses"]["q2_flue_gas_percent"]
