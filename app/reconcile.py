"""正反平衡对账（数据协调）。

问题：求一组测量值调整量 δ，使调整后正平衡效率与反平衡效率一致，
且调整量相对各自不确定度尽量小 —— 加权最小二乘，权取 1/σ²。

设 g(x) = η_正(x) − η_反(x)，J 为 g 对测量值 x 的梯度（数值微分），
Σ = diag(σ²)。标量约束 g(x+δ) = 0 下的最小范数解为

    δ = −g · ΣJ / (JᵀΣJ)

即每个测量分摊到的调整量正比于其方差与灵敏度 —— 不确定度越大、对差值
影响越大的测量，分摊越多。效率对测量值非线性，故以高斯-牛顿迭代至
|g| 收敛（< 1e-9），保证调整后两法真正一致。

判定与报告：
  |g| ≤ 容差                 -> consistent，全部调整量为零；
  所有 |δ_i| ≤ k·σ_i         -> adjusted，给出调整后测量值；
  任一 |δ_i| > k·σ_i 或迭代不收敛
                              -> exceeds_uncertainty_budget，仍给出最小范数解、
                                 各调整量的 σ 倍数与首要怀疑对象，由人工介入。
首要怀疑对象 = |δ_i|/σ_i 最大的测量。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

CONSISTENT = "consistent"
ADJUSTED = "adjusted"
EXCEEDS_BUDGET = "exceeds_uncertainty_budget"


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    difference_pp: float                 # 调整前两法差值（个百分点）
    adjustments: dict[str, float]        # 各测量的调整量（与测量同单位）
    adjustments_in_sigma: dict[str, float]  # 调整量相对各自不确定度的倍数
    adjusted_values: dict[str, float]    # 调整后测量值
    prime_suspect: str | None            # 首要怀疑对象
    max_sigma_multiple: float            # 判定用的 k

    def to_dict(self) -> dict:
        return asdict(self)


def reconcile(
    values: dict[str, float],
    sigmas: dict[str, float],
    diff_fn: Callable[[dict[str, float]], float],
    tolerance_pp: float,
    max_sigma_multiple: float,
    *,
    max_iter: int = 50,
    convergence_tol: float = 1e-9,
) -> ReconciliationResult:
    keys = list(values)
    zeros = {k: 0.0 for k in keys}
    g0 = diff_fn(values)

    if abs(g0) <= tolerance_pp:
        return ReconciliationResult(
            CONSISTENT, g0, zeros, dict(zeros), dict(values), None, max_sigma_multiple
        )

    adjusted = dict(values)
    total_delta = dict(zeros)
    converged = False

    for _ in range(max_iter):
        g = diff_fn(adjusted)
        grad: dict[str, float] = {}
        for k in keys:
            h = max(abs(adjusted[k]) * 1e-5, 1e-9)
            hi = dict(adjusted)
            lo = dict(adjusted)
            hi[k] += h
            lo[k] -= h
            grad[k] = (diff_fn(hi) - diff_fn(lo)) / (2.0 * h)
        denom = sum((grad[k] * sigmas[k]) ** 2 for k in keys)
        if denom <= 0.0:
            break  # 差值对所有测量均无灵敏度，无法分摊
        for k in keys:
            step = -g * sigmas[k] ** 2 * grad[k] / denom
            adjusted[k] += step
            total_delta[k] += step
        if abs(diff_fn(adjusted)) <= convergence_tol:
            converged = True
            break

    ratios = {
        k: (abs(total_delta[k]) / sigmas[k] if sigmas[k] > 0.0 else 0.0) for k in keys
    }
    suspect = max(keys, key=lambda k: ratios[k])
    feasible = converged and ratios[suspect] <= max_sigma_multiple
    status = ADJUSTED if feasible else EXCEEDS_BUDGET

    return ReconciliationResult(
        status, g0, total_delta, ratios, adjusted, suspect, max_sigma_multiple
    )
