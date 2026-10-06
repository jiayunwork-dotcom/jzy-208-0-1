"""正平衡（输入-输出法）锅炉效率。

    η = 有效输出热量 / 输入热量 × 100%

输出热量 = 主蒸汽吸热 + 再热蒸汽吸热 + 排污水带走热量（均相对给水焓）
输入热量 = 入炉煤量 × 收到基低位发热量
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import CalcConfig
from .schemas import CoalQuality, RunData


@dataclass(frozen=True)
class DirectResult:
    efficiency: float   # %
    output_heat: float  # kJ/h
    input_heat: float   # kJ/h

    def to_dict(self) -> dict:
        return asdict(self)


def compute(run: RunData, coal: CoalQuality, config: CalcConfig) -> DirectResult:
    output = (
        run.main_steam_flow * (run.main_steam_enthalpy - run.feedwater_enthalpy)
        + run.rh_steam_flow * (run.rh_outlet_enthalpy - run.rh_inlet_enthalpy)
        + run.blowdown_flow * (config.blowdown_water_enthalpy - run.feedwater_enthalpy)
    )
    input_heat = run.coal_flow * coal.qnet
    return DirectResult(100.0 * output / input_heat, output, input_heat)
