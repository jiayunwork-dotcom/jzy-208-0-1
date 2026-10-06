"""测试共用的数据构造器。

默认值取自一台 300 MW 等级机组的典型班次，且正/反平衡结果接近
（差值在默认容差 0.5 个百分点内），便于各测试在此基础上扰动。
"""
from __future__ import annotations

import copy

from app.schemas import DEFAULT_CONFIG


def make_run(**overrides) -> dict:
    data = {
        "coal_flow_tph": 142.0,
        "steam_flow_tph": 1000.0,
        "steam_enthalpy_kj_kg": 3400.0,
        "feedwater_enthalpy_kj_kg": 1050.0,
        "reheat_flow_tph": 850.0,
        "reheat_inlet_enthalpy_kj_kg": 3050.0,
        "reheat_outlet_enthalpy_kj_kg": 3550.0,
        "blowdown_flow_tph": 5.0,
        "flue_gas_temp_c": 135.0,
        "ambient_temp_c": 25.0,
        "o2_percent": 4.0,
        "co_percent": 0.01,
        "fly_ash_carbon_percent": 2.5,
        "slag_carbon_percent": 5.0,
        "fly_ash_share": 0.9,
        "slag_share": 0.1,
    }
    data.update(overrides)
    return data


def make_coal(**overrides) -> dict:
    data = {
        "carbon_percent": 55.0,
        "hydrogen_percent": 3.5,
        "oxygen_percent": 6.0,
        "nitrogen_percent": 1.0,
        "sulfur_percent": 0.8,
        "moisture_percent": 12.0,
        "ash_percent": 21.7,
        "qnet_kj_kg": 21000.0,
    }
    data.update(overrides)
    return data


def make_config(**overrides) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in overrides.items():
        if key == "uncertainties":
            config["uncertainties"]["relative"].update(value.get("relative", {}))
            config["uncertainties"]["absolute"].update(value.get("absolute", {}))
        else:
            config[key] = value
    return config
