"""正反平衡对账：把效率差额按不确定度分摊回各测量值。

模型
----
设测量值向量为 x，f(x) = eta_direct(x) - eta_indirect(x)。
寻找调整量 δ，使 f(x+δ) = 0，且调整尽量"对得起"各测量的不确定度 u_i：
在一阶泰勒近似下，这是一个加权最小二乘问题

    min  Σ (δ_i / u_i)^2
    s.t. Σ c_i δ_i = -f(x),   c_i = ∂f/∂x_i

解析解（高斯-马尔可夫意义下的最优线性无偏调整）：

    δ_i = -f(x) * c_i * u_i^2 / Σ_j (c_j * u_j)^2

取舍说明
--------
* 灵敏度 c_i 用中心差分数值求得，避免手写偏导出错，新增测量项时无需改公式。
* 每个测量都被允许调整，调整量天然与 (灵敏度 × 不确定度²) 成正比——
  不确定度大、灵敏度高的测量分到更多调整，这正是不确定度传播的意义。
* 若所有 |δ_i| <= sigma_limit * u_i，认为对账在不确定度范围内完成；
  否则照常给出最小二乘调整（它是"最不至于冤枉仪表"的解释），但把
  reconcilable_within_uncertainty 置为 False 并列出超限测量，
  提示差额已大到无法用正常计量误差解释，需人工排查（表计故障、
  皮带秤失准、化验错误等）。
* 两法已经一致（f(x)=0）时 δ 恒为零，首要怀疑对象为 None。
"""
from __future__ import annotations

from .direct import direct_efficiency
from .indirect import heat_losses
from .schemas import ABSOLUTE_UNCERTAINTY_FIELDS, RELATIVE_UNCERTAINTY_FIELDS

# 参与对账的测量（顺序即报告顺序）
MEASUREMENTS: tuple[str, ...] = RELATIVE_UNCERTAINTY_FIELDS + ABSOLUTE_UNCERTAINTY_FIELDS

# 各测量所在的数据集：qnet 属于煤质，其余属于运行数据
_COAL_FIELDS = {"qnet_kj_kg"}


def _value(run: dict, coal: dict, name: str) -> float:
    return coal[name] if name in _COAL_FIELDS else run[name]


def _set(run: dict, coal: dict, name: str, value: float) -> None:
    (coal if name in _COAL_FIELDS else run)[name] = value


def _uncertainty(config: dict, name: str, measured: float) -> float:
    unc = config["uncertainties"]
    if name in unc["relative"]:
        return abs(measured) * unc["relative"][name]
    return unc["absolute"][name]


def efficiency_difference(run: dict, coal: dict, config: dict) -> float:
    """f(x) = 正平衡效率 - 反平衡效率（百分点）。"""
    return (
        direct_efficiency(run, coal, config)["eta_percent"]
        - heat_losses(run, coal, config)["eta_percent"]
    )


def reconcile(run: dict, coal: dict, config: dict) -> dict:
    """对账主入口。run/coal/config 为已校验的 dict，函数不修改入参。

    采用迭代加权最小二乘（高斯-牛顿）：每轮在当前点数值线性化、按
    不确定度加权分摊差额，直到残差可忽略。权重（不确定度）固定取
    原始测量值处的值，保证分摊比例稳定。
    """
    run = dict(run)
    coal = dict(coal)

    measured = {name: _value(run, coal, name) for name in MEASUREMENTS}
    unc = {name: _uncertainty(config, name, measured[name]) for name in MEASUREMENTS}

    deltas = {name: 0.0 for name in MEASUREMENTS}
    singular = False
    for _ in range(5):
        f = efficiency_difference(run, coal, config)
        if abs(f) < 1e-9:
            break
        # 数值灵敏度 c_i = ∂f/∂x_i（中心差分）
        sens: dict[str, float] = {}
        for name in MEASUREMENTS:
            x = _value(run, coal, name)
            h = max(abs(x), unc[name], 1e-3) * 1e-6
            _set(run, coal, name, x + h)
            f_plus = efficiency_difference(run, coal, config)
            _set(run, coal, name, x - h)
            f_minus = efficiency_difference(run, coal, config)
            _set(run, coal, name, x)
            sens[name] = (f_plus - f_minus) / (2.0 * h)
        denom = sum((sens[n] * unc[n]) ** 2 for n in MEASUREMENTS)
        if denom <= 0.0:
            singular = True
            break
        for name in MEASUREMENTS:
            step = -f * sens[name] * unc[name] ** 2 / denom
            deltas[name] += step
            _set(run, coal, name, _value(run, coal, name) + step)

    if singular:
        return {
            "sigma_limit": config["sigma_limit"],
            "adjustments": [],
            "primary_suspect": None,
            "reconcilable_within_uncertainty": False,
            "violations": [],
            "residual_after_adjustment": efficiency_difference(run, coal, config),
            "reconciled_eta_percent": None,
            "note": "效率差对所有测量均不敏感，无法分摊调整量",
        }

    applied = any(d != 0.0 for d in deltas.values())

    adjustments = []
    worst_name, worst_ratio = None, 0.0
    for name in MEASUREMENTS:
        ratio = abs(deltas[name]) / unc[name] if unc[name] > 0 else 0.0
        if ratio > worst_ratio:
            worst_name, worst_ratio = name, ratio
        adjustments.append(
            {
                "name": name,
                "measured": measured[name],
                "adjusted": measured[name] + deltas[name],
                "delta": deltas[name],
                "uncertainty": unc[name],
                "delta_in_sigmas": deltas[name] / unc[name] if unc[name] > 0 else 0.0,
            }
        )

    if not applied:
        # 两法已一致：不调整、无怀疑对象
        worst_name = None

    limit = config["sigma_limit"]
    violations = [
        a["name"] for a in adjustments if abs(a["delta_in_sigmas"]) > limit
    ]

    # 校验：用调整后的测量值重算，残差应接近 0
    residual = efficiency_difference(run, coal, config)
    reconciled_eta = direct_efficiency(run, coal, config)["eta_percent"]

    note = None
    if violations:
        note = (
            "差额无法在各测量配置的不确定度范围内分摊完成；"
            "已给出最小二乘意义下最优的调整量，但下列测量超限，"
            "建议人工核查计量、化验与氧量表：" + ", ".join(violations)
        )

    return {
        "sigma_limit": limit,
        "adjustments": adjustments,
        "primary_suspect": worst_name,
        "reconcilable_within_uncertainty": not violations,
        "violations": violations,
        "residual_after_adjustment": residual,
        "reconciled_eta_percent": reconciled_eta,
        "note": note,
    }
