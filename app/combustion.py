"""燃烧计算：由收到基元素分析推理论空气量与烟气量，由排烟氧量定过量空气系数。

基准：1 kg 收到基燃料；气体体积为标准状态（0 °C、101.325 kPa）下的体积，单位 Nm³。
元素含量为收到基质量百分数（%）。

理论空气量工程公式：
    V0 = 0.0889·(C + 0.375·S) + 0.265·H − 0.0333·O     [Nm³/kg]
系数来源（空气按体积 O2 21% / N2 79%，1 kmol 气体标准状态 22.414 Nm³）：
    C + O2 -> CO2：1 kg 碳需氧 8/3 kg = 1.866 Nm³，折空气 1.866/0.21 ≈ 8.89 Nm³；
    H2 + ½O2 -> H2O：1 kg 氢折空气约 26.5 Nm³；S 按 0.375 倍碳当量；
    燃料自身含氧折抵相应空气量（1 kg 氧 ≈ 3.33 Nm³ 空气）。
"""
from __future__ import annotations

from dataclasses import dataclass


def theoretical_air(carbon: float, hydrogen: float, oxygen: float, sulfur: float) -> float:
    """理论空气量 V0 [Nm³/kg 燃料]。"""
    return 0.0889 * (carbon + 0.375 * sulfur) + 0.265 * hydrogen - 0.0333 * oxygen


def excess_air_ratio(flue_o2: float, flue_co: float) -> float:
    """过量空气系数，干烟气近似并做 CO 修正：α = 21 / (21 − (O2 − 0.5·CO))。

    CO 按"若燃尽尚需半倍体积的氧"折算；O2 = 0 且 CO = 0 时 α = 1。
    """
    denom = 21.0 - (flue_o2 - 0.5 * flue_co)
    if denom <= 0.0:
        raise ValueError(f"排烟氧量/CO 组合不合理: O2={flue_o2}%, CO={flue_co}%")
    return 21.0 / denom


@dataclass(frozen=True)
class FlueGasVolumes:
    theoretical_air: float  # V0，理论空气量 Nm³/kg
    ro2: float              # CO2 + SO2 体积 Nm³/kg
    nitrogen: float         # 理论氮气体积（空气带入 + 燃料氮）Nm³/kg
    water_vapor: float      # 理论水蒸气体积 Nm³/kg
    dry_gas: float          # 实际干烟气体积 = RO2 + N2 + (α−1)·V0
    steam: float            # 实际水蒸气 = 理论值 + 过量空气含湿修正


def flue_gas_volumes(
    carbon: float,
    hydrogen: float,
    oxygen: float,
    nitrogen: float,
    sulfur: float,
    moisture: float,
    alpha: float,
) -> FlueGasVolumes:
    v0 = theoretical_air(carbon, hydrogen, oxygen, sulfur)
    v_ro2 = 0.01866 * (carbon + 0.375 * sulfur)
    v_n2 = 0.79 * v0 + 0.008 * nitrogen
    v_h2o = 0.111 * hydrogen + 0.0124 * moisture + 0.0161 * v0
    dry = v_ro2 + v_n2 + (alpha - 1.0) * v0
    steam = v_h2o + 0.0161 * (alpha - 1.0) * v0
    return FlueGasVolumes(v0, v_ro2, v_n2, v_h2o, dry, steam)


# 气体平均容积比热容（0..t °C 的平均值）工程拟合：c(t) = a + b·t  [kJ/(Nm³·K)]
# 由 0–300 °C 标准摩尔定压热容数据拟合；相对 0 °C 的焓为 H(t) = a·t + b·t²  [kJ/Nm³]
_RO2_CP = (1.6560, 6.30e-4)    # CO2/SO2 等三原子气体
_AIR_CP = (1.2980, 8.70e-5)    # N2、O2、空气等双原子气体
_STEAM_CP = (1.4940, 2.70e-4)  # 水蒸气


def _gas_enthalpy(coeffs: tuple[float, float], t: float) -> float:
    a, b = coeffs
    return a * t + b * t * t


def ro2_enthalpy(t: float) -> float:
    """RO2 气体相对 0 °C 的焓 [kJ/Nm³]。"""
    return _gas_enthalpy(_RO2_CP, t)


def air_enthalpy(t: float) -> float:
    """双原子气体（N2/O2/空气）相对 0 °C 的焓 [kJ/Nm³]。"""
    return _gas_enthalpy(_AIR_CP, t)


def steam_enthalpy(t: float) -> float:
    """水蒸气相对 0 °C 的焓 [kJ/Nm³]。"""
    return _gas_enthalpy(_STEAM_CP, t)
