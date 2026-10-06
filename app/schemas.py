"""Pydantic 数据模型与入库校验。

所有"拒收并指出字段"的规则都集中在这里，由 FastAPI 统一转成 422 响应，
响应体的 detail 里带有出错字段名（loc）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 元素质量分数之和允许偏离 100% 的最大百分点数
ELEMENT_SUM_TOLERANCE = 0.5
# 灰渣比例之和允许偏离 1 的误差
ASH_SHARE_TOLERANCE = 1e-6


class RunData(BaseModel):
    """一个班次的运行数据。流量 t/h，焓 kJ/kg，温度 °C，浓度 %。"""

    coal_flow_tph: float = Field(gt=0, description="入炉煤量 t/h")
    steam_flow_tph: float = Field(gt=0, description="主蒸汽流量 t/h")
    steam_enthalpy_kj_kg: float = Field(description="主蒸汽焓 kJ/kg")
    feedwater_enthalpy_kj_kg: float = Field(description="给水焓 kJ/kg")
    reheat_flow_tph: float = Field(ge=0, description="再热蒸汽流量 t/h")
    reheat_inlet_enthalpy_kj_kg: float = Field(ge=0, description="再热器进口焓 kJ/kg")
    reheat_outlet_enthalpy_kj_kg: float = Field(ge=0, description="再热器出口焓 kJ/kg")
    blowdown_flow_tph: float = Field(ge=0, description="排污量 t/h")
    flue_gas_temp_c: float = Field(description="排烟温度 °C")
    ambient_temp_c: float = Field(description="环境温度 °C")
    o2_percent: float = Field(description="排烟氧量 %")
    co_percent: float = Field(ge=0, description="排烟一氧化碳浓度 %")
    fly_ash_carbon_percent: float = Field(ge=0, lt=100, description="飞灰含碳量 %")
    slag_carbon_percent: float = Field(ge=0, lt=100, description="炉渣含碳量 %")
    fly_ash_share: float = Field(ge=0, le=1, description="飞灰占灰渣比例")
    slag_share: float = Field(ge=0, le=1, description="炉渣占灰渣比例")

    @field_validator("o2_percent")
    @classmethod
    def _o2_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 21.0:
            raise ValueError(f"o2_percent 必须在 [0, 21] 区间内，当前值 {v}")
        return v

    @model_validator(mode="after")
    def _check(self) -> "RunData":
        if self.steam_enthalpy_kj_kg <= self.feedwater_enthalpy_kj_kg:
            raise ValueError(
                "steam_enthalpy_kj_kg 必须大于 feedwater_enthalpy_kj_kg"
            )
        if abs(self.fly_ash_share + self.slag_share - 1.0) > ASH_SHARE_TOLERANCE:
            raise ValueError(
                "fly_ash_share + slag_share 必须等于 1，"
                f"当前为 {self.fly_ash_share + self.slag_share}"
            )
        return self


class CoalQuality(BaseModel):
    """煤质化验（收到基），质量分数 %，低位发热量 kJ/kg。"""

    carbon_percent: float = Field(ge=0, le=100)
    hydrogen_percent: float = Field(ge=0, le=100)
    oxygen_percent: float = Field(ge=0, le=100)
    nitrogen_percent: float = Field(ge=0, le=100)
    sulfur_percent: float = Field(ge=0, le=100)
    moisture_percent: float = Field(ge=0, le=100)
    ash_percent: float = Field(ge=0, le=100)
    qnet_kj_kg: float = Field(description="收到基低位发热量 kJ/kg")

    @field_validator("qnet_kj_kg")
    @classmethod
    def _qnet_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"qnet_kj_kg 必须为正，当前值 {v}")
        return v

    @model_validator(mode="after")
    def _check(self) -> "CoalQuality":
        total = (
            self.carbon_percent
            + self.hydrogen_percent
            + self.oxygen_percent
            + self.nitrogen_percent
            + self.sulfur_percent
            + self.moisture_percent
            + self.ash_percent
        )
        if abs(total - 100.0) > ELEMENT_SUM_TOLERANCE:
            raise ValueError(
                "元素质量分数之和与 100% 的偏差超过 "
                f"{ELEMENT_SUM_TOLERANCE} 个百分点，当前之和为 {total}"
            )
        return self


# ---------------------------------------------------------------------------
# 配置（不确定度、容差、散热损失取法等）
# ---------------------------------------------------------------------------

# 以相对不确定度（占测量值的分数）给出的量
RELATIVE_UNCERTAINTY_FIELDS: tuple[str, ...] = (
    "coal_flow_tph",
    "steam_flow_tph",
    "steam_enthalpy_kj_kg",
    "feedwater_enthalpy_kj_kg",
    "reheat_flow_tph",
    "reheat_inlet_enthalpy_kj_kg",
    "reheat_outlet_enthalpy_kj_kg",
    "blowdown_flow_tph",
    "qnet_kj_kg",
)
# 以绝对不确定度（与测量值同单位）给出的量
ABSOLUTE_UNCERTAINTY_FIELDS: tuple[str, ...] = (
    "flue_gas_temp_c",
    "ambient_temp_c",
    "o2_percent",
    "co_percent",
    "fly_ash_carbon_percent",
    "slag_carbon_percent",
)


class Uncertainties(BaseModel):
    """每个参与对账的测量的不确定度。

    relative 中为分数（0.02 表示测量值的 2%），absolute 中为同单位绝对值。
    两组都必须完整给出且全部为正。
    """

    relative: dict[str, float]
    absolute: dict[str, float]

    @model_validator(mode="after")
    def _check(self) -> "Uncertainties":
        for group_name, required in (
            ("relative", RELATIVE_UNCERTAINTY_FIELDS),
            ("absolute", ABSOLUTE_UNCERTAINTY_FIELDS),
        ):
            values = getattr(self, group_name)
            for name in required:
                if name not in values:
                    raise ValueError(f"uncertainties.{group_name} 缺少字段 {name}")
                if values[name] <= 0:
                    raise ValueError(
                        f"uncertainties.{group_name}.{name} 必须为正，"
                        f"当前值 {values[name]}"
                    )
        return self


class AppConfig(BaseModel):
    """全局配置（每次修改产生新的配置版本）。

    散热损失 q5 取法（设计决定）：
      - q5_mode = "fixed"：直接取 q5_fixed_percent（默认，适用于负荷变化不大的机组）；
      - q5_mode = "load_scaled"：按额定负荷曲线折算，
        q5 = q5_rated_percent * rated_steam_flow_tph / 实际主蒸汽流量。
    """

    tolerance_percent: float = Field(
        gt=0, description="正/反平衡效率差容差（百分点），超过则触发对账"
    )
    sigma_limit: float = Field(
        default=3.0, gt=0, description="调整量相对不确定度的可接受倍数"
    )
    q5_mode: Literal["fixed", "load_scaled"] = "fixed"
    q5_fixed_percent: float = Field(default=0.5, ge=0)
    q5_rated_percent: float = Field(default=0.5, gt=0)
    rated_steam_flow_tph: float = Field(default=1000.0, gt=0)
    blowdown_water_enthalpy_kj_kg: float = Field(
        default=1150.0, description="排污水焓（近似取汽包压力下的饱和水焓）"
    )
    slag_temp_c: float = Field(default=600.0, description="炉渣排出温度 °C")
    uncertainties: Uncertainties


DEFAULT_CONFIG: dict = {
    "tolerance_percent": 0.5,
    "sigma_limit": 3.0,
    "q5_mode": "fixed",
    "q5_fixed_percent": 0.5,
    "q5_rated_percent": 0.5,
    "rated_steam_flow_tph": 1000.0,
    "blowdown_water_enthalpy_kj_kg": 1150.0,
    "slag_temp_c": 600.0,
    "uncertainties": {
        "relative": {
            "coal_flow_tph": 0.02,
            "steam_flow_tph": 0.01,
            "steam_enthalpy_kj_kg": 0.005,
            "feedwater_enthalpy_kj_kg": 0.005,
            "reheat_flow_tph": 0.01,
            "reheat_inlet_enthalpy_kj_kg": 0.005,
            "reheat_outlet_enthalpy_kj_kg": 0.005,
            "blowdown_flow_tph": 0.05,
            "qnet_kj_kg": 0.01,
        },
        "absolute": {
            "flue_gas_temp_c": 3.0,
            "ambient_temp_c": 2.0,
            "o2_percent": 0.3,
            "co_percent": 0.02,
            "fly_ash_carbon_percent": 0.5,
            "slag_carbon_percent": 0.5,
        },
    },
}
