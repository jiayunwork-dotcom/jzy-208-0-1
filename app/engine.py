"""计算编排：由运行数据 + 煤质 + 配置产出一个班次的完整计算结果（纯函数，不落库）。"""
from __future__ import annotations

from . import direct, indirect
from . import reconcile as rec
from .config import COAL_MEASUREMENTS, MEASUREMENT_KEYS, RUN_MEASUREMENTS, CalcConfig
from .schemas import CoalQuality, RunData

STATUS_PENDING = "pending_assay"
STATUS_COMPUTED = "computed"


def _flatten(run: RunData, coal: CoalQuality) -> dict[str, float]:
    """把参与对账的测量项摊平成 {名称: 数值}。灰渣份额为硬约束（和恒为 1），不参与。"""
    values = {k: getattr(run, k) for k in RUN_MEASUREMENTS}
    values.update(coal.model_dump())
    return values


def _rebuild(
    run: RunData, coal: CoalQuality, values: dict[str, float]
) -> tuple[RunData, CoalQuality]:
    adjusted_run = run.model_copy(update={k: values[k] for k in RUN_MEASUREMENTS})
    adjusted_coal = coal.model_copy(update={k: values[k] for k in COAL_MEASUREMENTS})
    return adjusted_run, adjusted_coal


def _diff_fn(run: RunData, coal: CoalQuality, config: CalcConfig):
    def f(values: dict[str, float]) -> float:
        r, c = _rebuild(run, coal, values)
        return (
            direct.compute(r, c, config).efficiency
            - indirect.compute(r, c, config).efficiency
        )

    return f


def _sigmas(values: dict[str, float], config: CalcConfig) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in MEASUREMENT_KEYS:
        unc = config.uncertainties[key]
        if unc.absolute is not None:
            sigma = unc.absolute
        else:
            sigma = unc.relative_pct / 100.0 * abs(values[key])
        out[key] = max(sigma, 1e-12)
    return out


def compute_shift_result(
    run: RunData, coal: CoalQuality | None, config: CalcConfig
) -> dict:
    """煤质未到 -> 只报"待化验"；否则给出正平衡、反平衡各项损失与对账结果。"""
    if coal is None:
        return {"status": STATUS_PENDING}

    d = direct.compute(run, coal, config)
    losses = indirect.compute(run, coal, config)

    values = _flatten(run, coal)
    sigmas = _sigmas(values, config)
    reconciliation = rec.reconcile(
        values, sigmas, _diff_fn(run, coal, config),
        config.tolerance_pp, config.max_sigma_multiple,
    )

    adj_run, adj_coal = _rebuild(run, coal, reconciliation.adjusted_values)
    adj_direct = direct.compute(adj_run, adj_coal, config).efficiency
    adj_indirect = indirect.compute(adj_run, adj_coal, config).efficiency

    return {
        "status": STATUS_COMPUTED,
        "direct": d.to_dict(),
        "indirect": losses.to_dict(),
        "reconciliation": {
            **reconciliation.to_dict(),
            "direct_efficiency_adjusted": adj_direct,
            "indirect_efficiency_adjusted": adj_indirect,
        },
    }
