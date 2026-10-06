"""燃烧计算单元测试（无需数据库）。"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.combustion import (
    excess_air_coefficient,
    flue_gas_volumes,
    theoretical_air_nm3_per_kg,
)


def test_pure_carbon_theoretical_air():
    """1 kg 纯碳完全燃烧的理论空气量约 8.89 Nm3。"""
    v0 = theoretical_air_nm3_per_kg(carbon=100.0, hydrogen=0.0, oxygen=0.0, sulfur=0.0)
    assert v0 == pytest.approx(8.89, abs=1e-9)


def test_excess_air_unity_at_zero_o2_and_zero_co():
    """排烟氧量为零、一氧化碳为零时过量空气系数应为 1。"""
    assert excess_air_coefficient(o2_percent=0.0, co_percent=0.0) == pytest.approx(1.0)


def test_excess_air_formula_typical_value():
    # alpha = 21 / (21 - O2)，O2=4% 时约 1.235
    assert excess_air_coefficient(4.0) == pytest.approx(21.0 / 17.0)


def test_excess_air_co_correction():
    """同样的氧量读数下，CO 越高说明未燃尽越多，有效过量空气系数越小。"""
    alpha_no_co = excess_air_coefficient(4.0, 0.0)
    alpha_with_co = excess_air_coefficient(4.0, 1.0)
    assert alpha_with_co < alpha_no_co
    # CO=0 时退化为纯氧量公式
    assert alpha_no_co == pytest.approx(21.0 / 17.0)


def test_excess_air_rejects_impossible_o2():
    with pytest.raises(ValueError):
        excess_air_coefficient(21.0)


def test_flue_gas_volumes_increase_with_alpha():
    v1 = flue_gas_volumes(55, 3.5, 6, 1, 0.8, 12, alpha=1.0)
    v2 = flue_gas_volumes(55, 3.5, 6, 1, 0.8, 12, alpha=1.3)
    assert v2.v_dry > v1.v_dry
    assert v2.v_h2o > v1.v_h2o
    # 理论量与 alpha 无关
    assert v2.v0 == v1.v0
    assert v2.v_ro2 == pytest.approx(v1.v_ro2)
    # 过量空气部分恰好是 (alpha-1)*V0
    assert v2.v_dry - v1.v_dry == pytest.approx(0.3 * v1.v0)


def test_flue_gas_volume_sanity():
    """RO2 与氮气理论量之和应等于 alpha=1 时的干烟气量。"""
    v = flue_gas_volumes(55, 3.5, 6, 1, 0.8, 12, alpha=1.0)
    assert v.v_dry == pytest.approx(v.v_ro2 + v.v_n2_theoretical)
    assert math.isfinite(v.v_total)
