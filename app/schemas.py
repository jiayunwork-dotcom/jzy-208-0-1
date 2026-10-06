"""班次运行数据与煤质化验的接口模型。

单位约定：
  流量（入炉煤量、各蒸汽流量、排污量）  kg/h
  焓                                    kJ/kg
  温度                                  °C
  排烟氧量 / CO                         %（干基体积分数）
  飞灰 / 炉渣含碳量                     %（质量分数）
  飞灰 / 炉渣份额                       小数，两者之和为 1
  煤质元素（碳氢氧氮硫水分灰分）         %（收到基质量分数）
  低位发热量                            kJ/kg（收到基）

字段级领域校验（元素和、氧量范围、焓关系、灰渣比例等）见 validation.py。
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class RunData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shift_date: date = Field(description="班次日期，用于月度归集")
    coal_flow: float = Field(description="入炉煤量 kg/h")
    main_steam_flow: float = Field(description="主蒸汽流量 kg/h")
    main_steam_enthalpy: float = Field(description="主蒸汽焓 kJ/kg")
    feedwater_enthalpy: float = Field(description="给水焓 kJ/kg")
    rh_steam_flow: float = Field(description="再热蒸汽流量 kg/h")
    rh_inlet_enthalpy: float = Field(description="再热蒸汽进口焓 kJ/kg")
    rh_outlet_enthalpy: float = Field(description="再热蒸汽出口焓 kJ/kg")
    blowdown_flow: float = Field(description="排污量 kg/h")
    flue_gas_temp: float = Field(description="排烟温度 °C")
    ambient_temp: float = Field(description="环境温度 °C")
    flue_o2: float = Field(description="排烟氧量（干基）%")
    flue_co: float = Field(description="排烟 CO（干基）%")
    fly_ash_carbon: float = Field(description="飞灰含碳量 %")
    slag_carbon: float = Field(description="炉渣含碳量 %")
    fly_ash_share: float = Field(description="飞灰份额（占灰渣总量）")
    slag_share: float = Field(description="炉渣份额（占灰渣总量）")


class CoalQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carbon: float = Field(description="收到基碳 %")
    hydrogen: float = Field(description="收到基氢 %")
    oxygen: float = Field(description="收到基氧 %")
    nitrogen: float = Field(description="收到基氮 %")
    sulfur: float = Field(description="收到基硫 %")
    moisture: float = Field(description="收到基水分 %")
    ash: float = Field(description="收到基灰分 %")
    qnet: float = Field(description="收到基低位发热量 kJ/kg")
