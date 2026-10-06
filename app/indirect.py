"""反平衡（热损失法）锅炉效率。

eta = 100 - (q2 + q3 + q4 + q5 + q6)

  q2 排烟热损失        —— 由烟气量与排烟温度/环境温度差决定
  q3 化学不完全燃烧损失 —— 由干烟气量与 CO 浓度决定
  q4 机械不完全燃烧损失 —— 由灰渣量与飞灰/炉渣含碳量决定
  q5 散热损失          —— 取法见配置（固定值或按额定负荷折算）
  q6 灰渣物理热损失    —— 由灰渣量、排出温度决定

烟气量由元素分析推出（见 combustion 模块），过量空气系数由排烟氧量确定。
q2、q3 按 GB/T 10184 的习惯乘以 (1 - q4/100) 修正（未燃尽碳不生成烟气）。
"""
from __future__ import annotations

from .combustion import excess_air_coefficient, flue_gas_volumes

# 干烟气平均定压比热 kJ/(Nm3·K)（排烟温度区间的工程取值）
CP_DRY_FLUE_GAS = 1.38
# 水蒸气平均定压比热 kJ/(Nm3·K)
CP_STEAM = 1.51
# CO 低位发热量 kJ/Nm3
CO_COMBUSTION_HEAT = 12636.0
# 纯碳发热量 kJ/kg（用于机械不完全燃烧损失）
CARBON_COMBUSTION_HEAT = 32866.0
# 灰渣平均比热 kJ/(kg·K)
ASH_SPECIFIC_HEAT = 0.96


def heat_losses(run: dict, coal: dict, config: dict) -> dict:
    """计算各项热损失与反平衡效率，返回明细。"""
    qnet = coal["qnet_kj_kg"]
    ash = coal["ash_percent"]

    alpha = excess_air_coefficient(run["o2_percent"], run["co_percent"])
    vol = flue_gas_volumes(
        carbon=coal["carbon_percent"],
        hydrogen=coal["hydrogen_percent"],
        oxygen=coal["oxygen_percent"],
        nitrogen=coal["nitrogen_percent"],
        sulfur=coal["sulfur_percent"],
        moisture=coal["moisture_percent"],
        alpha=alpha,
    )

    delta_t = run["flue_gas_temp_c"] - run["ambient_temp_c"]

    # --- q4 机械不完全燃烧 -------------------------------------------------
    # 每 kg 燃料的灰分中未燃尽碳的质量（飞灰/炉渣分别按自身含碳量折算）
    unburnt_carbon = (ash / 100.0) * (
        run["fly_ash_share"] * run["fly_ash_carbon_percent"] / (100.0 - run["fly_ash_carbon_percent"])
        + run["slag_share"] * run["slag_carbon_percent"] / (100.0 - run["slag_carbon_percent"])
    )
    q4_heat = CARBON_COMBUSTION_HEAT * unburnt_carbon
    q4 = 100.0 * q4_heat / qnet
    burn_correction = 1.0 - q4 / 100.0

    # --- q2 排烟热损失 -----------------------------------------------------
    q2_heat = (
        vol.v_dry * CP_DRY_FLUE_GAS + vol.v_h2o * CP_STEAM
    ) * delta_t
    q2 = 100.0 * q2_heat / qnet * burn_correction

    # --- q3 化学不完全燃烧 --------------------------------------------------
    q3_heat = vol.v_dry * (run["co_percent"] / 100.0) * CO_COMBUSTION_HEAT
    q3 = 100.0 * q3_heat / qnet * burn_correction

    # --- q5 散热损失 --------------------------------------------------------
    # 设计决定：默认取配置中的固定值；配置为 load_scaled 时按额定负荷折算，
    # q5 = q5_rated * D_rated / D_actual（负荷越低，相对散热越大）。
    if config["q5_mode"] == "load_scaled":
        q5 = config["q5_rated_percent"] * config["rated_steam_flow_tph"] / run["steam_flow_tph"]
    else:
        q5 = config["q5_fixed_percent"]

    # --- q6 灰渣物理热损失 ---------------------------------------------------
    # 飞灰按排烟温度排出，炉渣按配置温度排出
    q6_heat = (ash / 100.0) * ASH_SPECIFIC_HEAT * (
        run["fly_ash_share"] * delta_t
        + run["slag_share"] * (config["slag_temp_c"] - run["ambient_temp_c"])
    )
    q6 = 100.0 * q6_heat / qnet

    eta = 100.0 - (q2 + q3 + q4 + q5 + q6)
    return {
        "eta_percent": eta,
        "losses": {
            "q2_flue_gas_percent": q2,
            "q3_chemical_incomplete_percent": q3,
            "q4_mechanical_incomplete_percent": q4,
            "q5_radiation_percent": q5,
            "q6_ash_physical_percent": q6,
        },
        "combustion": {
            "alpha": alpha,
            "v0_nm3_kg": vol.v0,
            "v_dry_nm3_kg": vol.v_dry,
            "v_h2o_nm3_kg": vol.v_h2o,
            "v_ro2_nm3_kg": vol.v_ro2,
        },
    }
