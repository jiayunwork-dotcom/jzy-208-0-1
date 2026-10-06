"""燃烧计算：理论空气量、过量空气系数、烟气量。

所有量均按 1 kg 收到基燃料计算，元素成分为质量分数百分数（0~100）。
采用锅炉行业惯用的经验系数体系：
  碳   0.0889 Nm3/kg（每 1% 收到基碳）
  硫   按 0.375 折合为碳当量
  氢   0.265，氧  -0.0333
核对基准：1 kg 纯碳（C=100%）完全燃烧的理论空气量 = 0.0889*100 = 8.89 Nm3。
"""
from __future__ import annotations

from dataclasses import dataclass

# 每 1% 收到基成分对应的理论空气量系数 (Nm3/kg)
K_CARBON = 0.0889
K_SULFUR_EQUIV = 0.375  # 硫折碳当量系数
K_HYDROGEN = 0.265
K_OXYGEN = -0.0333

AIR_O2_FRACTION = 0.21  # 空气中氧体积分数
AIR_N2_FRACTION = 0.79  # 空气中氮体积分数
AIR_MOISTURE_NM3 = 0.0161  # 每 Nm3 干空气带入的水蒸气


def theoretical_air_nm3_per_kg(
    carbon: float, hydrogen: float, oxygen: float, sulfur: float
) -> float:
    """理论空气量 V0 (Nm3/kg 燃料)，成分为质量分数百分数。"""
    return (
        K_CARBON * (carbon + K_SULFUR_EQUIV * sulfur)
        + K_HYDROGEN * hydrogen
        + K_OXYGEN * oxygen
    )


def excess_air_coefficient(o2_percent: float, co_percent: float = 0.0) -> float:
    """由排烟氧量确定过量空气系数 alpha。

    简化公式 alpha = 21 / (21 - O2')，其中 O2' = O2 - 0.5*CO 把未燃尽的
    一氧化碳折算回尚需的氧。O2=0 且 CO=0 时 alpha = 1（完全燃烧、无过量空气）。
    """
    o2_effective = max(o2_percent - 0.5 * co_percent, 0.0)
    if o2_effective >= 21.0:
        raise ValueError(f"折算后排烟氧量 {o2_effective}% 不低于 21%，无法确定过量空气系数")
    return 21.0 / (21.0 - o2_effective)


@dataclass(frozen=True)
class FlueGasVolumes:
    """1 kg 燃料产生的烟气量 (Nm3/kg)。"""

    v0: float  # 理论空气量
    v_ro2: float  # 三原子气体（CO2+SO2）
    v_n2_theoretical: float  # 理论氮气量
    v_h2o_theoretical: float  # 理论水蒸气量
    v_dry: float  # 实际干烟气量（含过量空气）
    v_h2o: float  # 实际水蒸气量（含过量空气带湿）

    @property
    def v_total(self) -> float:
        return self.v_dry + self.v_h2o


def flue_gas_volumes(
    carbon: float,
    hydrogen: float,
    oxygen: float,
    nitrogen: float,
    sulfur: float,
    moisture: float,
    alpha: float,
) -> FlueGasVolumes:
    """由元素分析和过量空气系数推烟气量。"""
    v0 = theoretical_air_nm3_per_kg(carbon, hydrogen, oxygen, sulfur)
    v_ro2 = 0.01866 * (carbon + K_SULFUR_EQUIV * sulfur)
    v_n2 = 0.008 * nitrogen + AIR_N2_FRACTION * v0
    v_h2o = 0.111 * hydrogen + 0.0124 * moisture + AIR_MOISTURE_NM3 * v0
    excess = (alpha - 1.0) * v0
    return FlueGasVolumes(
        v0=v0,
        v_ro2=v_ro2,
        v_n2_theoretical=v_n2,
        v_h2o_theoretical=v_h2o,
        v_dry=v_ro2 + v_n2 + excess,
        v_h2o=v_h2o + AIR_MOISTURE_NM3 * excess,
    )
