"""反平衡各项损失单元测试。"""
import pytest

from app import indirect
from app.config import CalcConfig
from app.schemas import CoalQuality, RunData

from conftest import SAMPLE_COAL, SAMPLE_RUN


def _losses(run_overrides=None, coal_overrides=None, config=None):
    run = RunData(**{**SAMPLE_RUN, **(run_overrides or {})})
    coal = CoalQuality(**{**SAMPLE_COAL, **(coal_overrides or {})})
    return indirect.compute(run, coal, config or CalcConfig())


def test_flue_gas_loss_increases_with_temperature():
    """其他条件不变，排烟温度升高时排烟损失增大。"""
    cool = _losses({"flue_gas_temp": 120.0})
    hot = _losses({"flue_gas_temp": 160.0})
    assert hot.q2_flue_gas > cool.q2_flue_gas
    assert hot.efficiency < cool.efficiency


def test_mechanical_loss_with_zero_fly_ash_carbon_only_slag_remains():
    """飞灰含碳为零时，机械不完全燃烧损失只剩炉渣那部分。"""
    losses = _losses({"fly_ash_carbon": 0.0})
    config = CalcConfig()
    run = RunData(**{**SAMPLE_RUN, "fly_ash_carbon": 0.0})
    coal = CoalQuality(**SAMPLE_COAL)
    expected = (
        100.0
        * (coal.ash / 100.0)
        * run.slag_share
        * run.slag_carbon
        / (100.0 - run.slag_carbon)
        * config.carbon_heating_value
        / coal.qnet
    )
    assert losses.q4_mechanical_incomplete == pytest.approx(expected)
    assert losses.q4_mechanical_incomplete > 0.0


def test_mechanical_loss_increases_with_fly_ash_carbon():
    low = _losses({"fly_ash_carbon": 1.0})
    high = _losses({"fly_ash_carbon": 4.0})
    assert high.q4_mechanical_incomplete > low.q4_mechanical_incomplete


def test_efficiency_is_100_minus_all_losses():
    losses = _losses()
    total = (
        losses.q2_flue_gas
        + losses.q3_chemical_incomplete
        + losses.q4_mechanical_incomplete
        + losses.q5_radiation
        + losses.q6_ash_physical
    )
    assert losses.efficiency == pytest.approx(100.0 - total)
    # 样例数据的量级 sanity check：大型煤粉炉反平衡效率 90% 上下
    assert 88.0 < losses.efficiency < 96.0


def test_excess_air_ratio_flows_into_result():
    losses = _losses({"flue_o2": 5.5, "flue_co": 0.0})
    assert losses.excess_air_ratio == pytest.approx(21.0 / (21.0 - 5.5))


def test_radiation_loss_load_curve_mode():
    from app.config import CalcConfig, RadiationLossConfig

    config = CalcConfig(
        radiation_loss=RadiationLossConfig(
            mode="load_curve", rated_value=0.5, rated_steam_flow=1_000_000.0
        )
    )
    rated = _losses(config=config)
    half_load = _losses({"main_steam_flow": 500_000.0}, config=config)
    assert rated.q5_radiation == pytest.approx(0.5)
    assert half_load.q5_radiation == pytest.approx(1.0)
