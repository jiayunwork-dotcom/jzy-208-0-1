"""反平衡（热损失法）锅炉效率。

    η = 100 − (q2 + q3 + q4 + q5 + q6)      [%]

各项损失以 1 kg 收到基燃料为基准，表示为占收到基低位发热量的百分比：
  q2 排烟热损失        干烟气与水蒸气相对环境温度的焓差
  q3 化学不完全燃烧    干烟气中 CO 未燃尽带走的发热量
  q4 机械不完全燃烧    飞灰、炉渣残碳按纯碳发热量折算
  q5 散热损失          固定值或按额定负荷折算（取法见配置 RadiationLossConfig）
  q6 灰渣物理热损失    飞灰按排烟温度、炉渣按排渣温度带出的显热
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import combustion
from .config import CalcConfig
from .schemas import CoalQuality, RunData


@dataclass(frozen=True)
class LossBreakdown:
    q2_flue_gas: float
    q3_chemical_incomplete: float
    q4_mechanical_incomplete: float
    q5_radiation: float
    q6_ash_physical: float
    efficiency: float
    excess_air_ratio: float
    theoretical_air: float
    dry_flue_gas_volume: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute(run: RunData, coal: CoalQuality, config: CalcConfig) -> LossBreakdown:
    alpha = combustion.excess_air_ratio(run.flue_o2, run.flue_co)
    vol = combustion.flue_gas_volumes(
        coal.carbon, coal.hydrogen, coal.oxygen, coal.nitrogen,
        coal.sulfur, coal.moisture, alpha,
    )
    t_gas = run.flue_gas_temp
    t_amb = run.ambient_temp
    qnet = coal.qnet

    # q2 排烟热损失
    h_dry = (
        vol.ro2 * (combustion.ro2_enthalpy(t_gas) - combustion.ro2_enthalpy(t_amb))
        + (vol.dry_gas - vol.ro2) * (combustion.air_enthalpy(t_gas) - combustion.air_enthalpy(t_amb))
    )
    h_steam = vol.steam * (combustion.steam_enthalpy(t_gas) - combustion.steam_enthalpy(t_amb))
    q2 = 100.0 * (h_dry + h_steam) / qnet

    # q3 化学不完全燃烧热损失
    q3 = 100.0 * vol.dry_gas * (run.flue_co / 100.0) * config.co_heating_value / qnet

    # q4 机械不完全燃烧热损失：灰渣残碳 = 灰分 × Σ 份额·C/(100−C)
    unburned_carbon = (coal.ash / 100.0) * (
        run.fly_ash_share * run.fly_ash_carbon / (100.0 - run.fly_ash_carbon)
        + run.slag_share * run.slag_carbon / (100.0 - run.slag_carbon)
    )
    q4 = 100.0 * unburned_carbon * config.carbon_heating_value / qnet

    # q5 散热损失
    rl = config.radiation_loss
    if rl.mode == "fixed":
        q5 = rl.fixed_value
    else:  # load_curve：按额定负荷折算
        q5 = rl.rated_value * rl.rated_steam_flow / run.main_steam_flow

    # q6 灰渣物理热损失
    sensible = (coal.ash / 100.0) * config.ash_specific_heat * (
        run.fly_ash_share * (t_gas - t_amb)
        + run.slag_share * (config.slag_temp - t_amb)
    )
    q6 = 100.0 * sensible / qnet

    efficiency = 100.0 - (q2 + q3 + q4 + q5 + q6)
    return LossBreakdown(
        q2_flue_gas=q2,
        q3_chemical_incomplete=q3,
        q4_mechanical_incomplete=q4,
        q5_radiation=q5,
        q6_ash_physical=q6,
        efficiency=efficiency,
        excess_air_ratio=alpha,
        theoretical_air=vol.theoretical_air,
        dry_flue_gas_volume=vol.dry_gas,
    )
