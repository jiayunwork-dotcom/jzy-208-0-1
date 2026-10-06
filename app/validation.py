"""领域校验：不合法数据拒收并指明字段。

校验错误以 [{"field": ..., "message": ...}] 形式抛出，接口层转成 422。
"""
from __future__ import annotations

from .schemas import CoalQuality, RunData

# 收到基各元素质量分数之和相对 100% 的允许偏差（个百分点）
ELEMENT_SUM_TOLERANCE_PP = 0.5
# 灰渣比例之和相对 1 的允许偏差
ASH_SHARE_TOLERANCE = 1e-6


class DomainValidationError(Exception):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__(str(errors))


def _err(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


def validate_run_data(run: RunData) -> None:
    errors: list[dict[str, str]] = []

    if not 0.0 <= run.flue_o2 <= 21.0:
        errors.append(_err("flue_o2", f"排烟氧量须在 0~21% 之间，实际 {run.flue_o2}"))
    if run.flue_co < 0.0:
        errors.append(_err("flue_co", "排烟 CO 不能为负"))
    if 0.0 <= run.flue_o2 <= 21.0 and run.flue_co >= 0.0:
        if 21.0 - (run.flue_o2 - 0.5 * run.flue_co) <= 0.0:
            errors.append(
                _err("flue_o2", "排烟氧量/CO 组合无法确定过量空气系数（21 − (O2 − 0.5·CO) ≤ 0）")
            )
    if run.main_steam_enthalpy <= run.feedwater_enthalpy:
        errors.append(
            _err(
                "main_steam_enthalpy",
                f"主蒸汽焓({run.main_steam_enthalpy})必须大于给水焓({run.feedwater_enthalpy})",
            )
        )
    if abs(run.fly_ash_share + run.slag_share - 1.0) > ASH_SHARE_TOLERANCE:
        errors.append(
            _err(
                "fly_ash_share",
                f"飞灰份额({run.fly_ash_share})与炉渣份额({run.slag_share})之和必须为 1",
            )
        )
    if run.coal_flow <= 0.0:
        errors.append(_err("coal_flow", "入炉煤量必须为正"))
    for f in ("main_steam_flow", "rh_steam_flow", "blowdown_flow"):
        if getattr(run, f) < 0.0:
            errors.append(_err(f, "流量不能为负"))
    for f in ("fly_ash_carbon", "slag_carbon"):
        v = getattr(run, f)
        if not 0.0 <= v < 100.0:
            errors.append(_err(f, f"含碳量须在 [0, 100)% 之间，实际 {v}"))
    for f in ("fly_ash_share", "slag_share"):
        v = getattr(run, f)
        if not 0.0 <= v <= 1.0:
            errors.append(_err(f, f"份额须在 [0, 1] 之间，实际 {v}"))

    if errors:
        raise DomainValidationError(errors)


def validate_coal_quality(coal: CoalQuality) -> None:
    errors: list[dict[str, str]] = []

    total = (
        coal.carbon + coal.hydrogen + coal.oxygen + coal.nitrogen
        + coal.sulfur + coal.moisture + coal.ash
    )
    if abs(total - 100.0) > ELEMENT_SUM_TOLERANCE_PP:
        errors.append(
            _err(
                "elemental_sum",
                f"收到基各元素质量分数之和为 {total:.3f}%，与 100% 的偏差超过 "
                f"{ELEMENT_SUM_TOLERANCE_PP} 个百分点",
            )
        )
    if coal.qnet <= 0.0:
        errors.append(_err("qnet", f"低位发热量必须为正，实际 {coal.qnet}"))
    for f in ("carbon", "hydrogen", "oxygen", "nitrogen", "sulfur", "moisture", "ash"):
        v = getattr(coal, f)
        if not 0.0 <= v <= 100.0:
            errors.append(_err(f, f"质量分数须在 0~100% 之间，实际 {v}"))

    if errors:
        raise DomainValidationError(errors)
