"""计算配置：不确定度、对账容差、散热损失取法等，整体版本化存储。

配置整体作为一个版本写入 config_versions；任何修改产生新版本并触发全部班次重算。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 参与对账调整的测量项。灰渣份额之和恒为 1（硬约束），不参与调整。
RUN_MEASUREMENTS: list[str] = [
    "coal_flow",
    "main_steam_flow",
    "main_steam_enthalpy",
    "feedwater_enthalpy",
    "rh_steam_flow",
    "rh_inlet_enthalpy",
    "rh_outlet_enthalpy",
    "blowdown_flow",
    "flue_gas_temp",
    "ambient_temp",
    "flue_o2",
    "flue_co",
    "fly_ash_carbon",
    "slag_carbon",
]
COAL_MEASUREMENTS: list[str] = [
    "qnet",
    "carbon",
    "hydrogen",
    "oxygen",
    "nitrogen",
    "sulfur",
    "moisture",
    "ash",
]
MEASUREMENT_KEYS: list[str] = RUN_MEASUREMENTS + COAL_MEASUREMENTS


class Uncertainty(BaseModel):
    """单个测量的不确定度（1σ）：相对读数百分比或绝对值，二者取一且必须为正。"""

    relative_pct: float | None = Field(default=None, gt=0, description="相对不确定度 %")
    absolute: float | None = Field(default=None, gt=0, description="绝对不确定度（与测量同单位）")

    @model_validator(mode="after")
    def _exactly_one(self) -> "Uncertainty":
        if (self.relative_pct is None) == (self.absolute is None):
            raise ValueError("relative_pct 与 absolute 必须且只能给一个")
        return self


# 出厂默认不确定度，可在配置中按项覆盖。
DEFAULT_UNCERTAINTIES: dict[str, Uncertainty] = {
    # 运行数据
    "coal_flow": Uncertainty(relative_pct=2.0),
    "main_steam_flow": Uncertainty(relative_pct=1.0),
    "main_steam_enthalpy": Uncertainty(relative_pct=0.5),
    "feedwater_enthalpy": Uncertainty(relative_pct=0.5),
    "rh_steam_flow": Uncertainty(relative_pct=1.0),
    "rh_inlet_enthalpy": Uncertainty(relative_pct=0.5),
    "rh_outlet_enthalpy": Uncertainty(relative_pct=0.5),
    "blowdown_flow": Uncertainty(relative_pct=5.0),
    "flue_gas_temp": Uncertainty(absolute=3.0),
    "ambient_temp": Uncertainty(absolute=2.0),
    "flue_o2": Uncertainty(absolute=0.3),
    "flue_co": Uncertainty(absolute=0.005),
    "fly_ash_carbon": Uncertainty(absolute=0.3),
    "slag_carbon": Uncertainty(absolute=0.5),
    # 煤质化验
    "qnet": Uncertainty(relative_pct=1.0),
    "carbon": Uncertainty(relative_pct=2.0),
    "hydrogen": Uncertainty(relative_pct=3.0),
    "oxygen": Uncertainty(relative_pct=5.0),
    "nitrogen": Uncertainty(relative_pct=5.0),
    "sulfur": Uncertainty(relative_pct=5.0),
    "moisture": Uncertainty(relative_pct=2.0),
    "ash": Uncertainty(relative_pct=2.0),
}


class RadiationLossConfig(BaseModel):
    """q5 散热损失取法（本服务的设计决定，见 README）：

    fixed      —— 固定值 fixed_value（%），默认方式；
    load_curve —— 按额定负荷折算：q5 = rated_value × rated_steam_flow / 实际主蒸汽流量。
    """

    mode: Literal["fixed", "load_curve"] = "fixed"
    fixed_value: float = Field(default=0.5, ge=0, description="固定散热损失 %")
    rated_value: float = Field(default=0.5, ge=0, description="额定负荷下散热损失 %")
    rated_steam_flow: float = Field(default=1_000_000.0, gt=0, description="额定主蒸汽流量 kg/h")


class CalcConfig(BaseModel):
    tolerance_pp: float = Field(
        default=0.5, gt=0, description="正反平衡差值容差（个百分点），不超过则判为一致"
    )
    max_sigma_multiple: float = Field(
        default=3.0, gt=0, description="调整量允许的最大不确定度倍数 k，超过则报告超出不确定度预算"
    )
    uncertainties: dict[str, Uncertainty] = Field(
        default_factory=lambda: dict(DEFAULT_UNCERTAINTIES)
    )
    radiation_loss: RadiationLossConfig = Field(default_factory=RadiationLossConfig)
    slag_temp: float = Field(default=600.0, description="排渣温度 °C（q6 用）")
    ash_specific_heat: float = Field(default=0.96, gt=0, description="灰渣比热容 kJ/(kg·K)")
    blowdown_water_enthalpy: float = Field(
        default=1400.0, description="排污水焓 kJ/kg（汽包压力下的饱和水，正平衡用）"
    )
    co_heating_value: float = Field(default=12636.0, gt=0, description="CO 发热量 kJ/Nm³")
    carbon_heating_value: float = Field(default=32866.0, gt=0, description="纯碳发热量 kJ/kg")

    @model_validator(mode="after")
    def _fill_and_check_uncertainties(self) -> "CalcConfig":
        unknown = sorted(set(self.uncertainties) - set(MEASUREMENT_KEYS))
        if unknown:
            raise ValueError(f"未知测量项: {unknown}")
        merged = dict(DEFAULT_UNCERTAINTIES)
        merged.update(self.uncertainties)
        self.uncertainties = merged
        return self
