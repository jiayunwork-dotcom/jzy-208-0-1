"""正平衡（输入-输出法）锅炉效率。

eta = 有效输出热量 / 燃料输入热量 * 100%

有效输出 = 主蒸汽吸热 + 再热蒸汽吸热 - 排污水带走的热量
输入     = 入炉煤量 * 收到基低位发热量

所有流量取同一单位（t/h），焓 kJ/kg，发热量 kJ/kg，比值无量纲。
"""
from __future__ import annotations


def direct_efficiency(run: dict, coal: dict, config: dict) -> dict:
    """返回正平衡效率及中间量。"""
    output_heat = (
        run["steam_flow_tph"]
        * (run["steam_enthalpy_kj_kg"] - run["feedwater_enthalpy_kj_kg"])
        + run["reheat_flow_tph"]
        * (run["reheat_outlet_enthalpy_kj_kg"] - run["reheat_inlet_enthalpy_kj_kg"])
        - run["blowdown_flow_tph"]
        * (config["blowdown_water_enthalpy_kj_kg"] - run["feedwater_enthalpy_kj_kg"])
    )
    input_heat = run["coal_flow_tph"] * coal["qnet_kj_kg"]
    eta = 100.0 * output_heat / input_heat
    return {
        "eta_percent": eta,
        "output_heat_kj_h": output_heat,
        "input_heat_kj_h": input_heat,
    }
