"""燃烧计算单元测试。"""
import pytest

from app.combustion import excess_air_ratio, flue_gas_volumes, theoretical_air


def test_pure_carbon_theoretical_air():
    """1 kg 纯碳完全燃烧的理论空气量约 8.89 Nm³。"""
    assert theoretical_air(carbon=100.0, hydrogen=0.0, oxygen=0.0, sulfur=0.0) == pytest.approx(
        8.89, abs=1e-2
    )


def test_excess_air_ratio_is_one_when_no_o2_and_no_co():
    """排烟氧量为零、一氧化碳为零时过量空气系数应为 1。"""
    assert excess_air_ratio(flue_o2=0.0, flue_co=0.0) == pytest.approx(1.0)


def test_excess_air_ratio_typical():
    assert excess_air_ratio(flue_o2=6.0, flue_co=0.0) == pytest.approx(1.4)


def test_excess_air_ratio_co_correction():
    """同样氧量下，CO 越高说明燃烧越不足，折算的过量空气系数越低。"""
    assert excess_air_ratio(3.0, 0.2) < excess_air_ratio(3.0, 0.0)


def test_excess_air_ratio_rejects_impossible_combination():
    with pytest.raises(ValueError):
        excess_air_ratio(flue_o2=21.0, flue_co=0.0)


def test_flue_gas_volumes_pure_carbon():
    """纯碳燃烧：RO2 与理论空气量符合化学计量关系。"""
    vol = flue_gas_volumes(
        carbon=100.0, hydrogen=0.0, oxygen=0.0, nitrogen=0.0,
        sulfur=0.0, moisture=0.0, alpha=1.0,
    )
    assert vol.theoretical_air == pytest.approx(8.89, abs=1e-2)
    assert vol.ro2 == pytest.approx(1.866, abs=1e-3)
    # α=1 时干烟气 = RO2 + 理论氮
    assert vol.dry_gas == pytest.approx(vol.ro2 + vol.nitrogen)
