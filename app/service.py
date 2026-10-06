"""业务编排：班次计算、配置变更重算、月度汇总与修订。

事务与并发约定
--------------
* 每个写接口在一个事务内完成：取班次咨询锁 → 写数据版本 → 若运行数据与
  煤质齐备则算出新计算版本 → 重算所在月草稿。锁保证同一班次的并发写入
  串行执行，后提交的事务读到的一定是双方都已落库的最新版本，因此最新
  计算版本永远不会停留在"只基于其中一方"的中间状态。
* 月度草稿采用"变化即整体重算"策略：任何班次产生新计算版本时，用该月
  所有班次的最新计算版本从头汇总。这天然保证月度值与"用当前所有最新
  版本从头汇总"完全一致。
* 月度权重取各班的燃料输入热量（入炉煤量 × 低位发热量）。
"""
from __future__ import annotations

from psycopg_pool import ConnectionPool

from . import repository as repo
from .direct import direct_efficiency
from .indirect import heat_losses
from .reconcile import reconcile


# ---------------------------------------------------------------------------
# 班次计算
# ---------------------------------------------------------------------------


def compute_shift_result(
    shift_id: str,
    run: dict,
    coal: dict,
    config: dict,
    run_data_version: int,
    coal_quality_version: int,
    config_version: int,
) -> dict:
    """用给定版本的数据算出完整结果（纯函数，便于测试）。"""
    direct = direct_efficiency(run, coal, config)
    indirect = heat_losses(run, coal, config)
    diff = direct["eta_percent"] - indirect["eta_percent"]
    within = abs(diff) <= config["tolerance_percent"]
    reconciliation = None if within else reconcile(run, coal, config)
    return {
        "shift_id": shift_id,
        "status": "ok",
        "run_data_version": run_data_version,
        "coal_quality_version": coal_quality_version,
        "config_version": config_version,
        "direct": direct,
        "indirect": indirect,
        "difference_percent": diff,
        "tolerance_percent": config["tolerance_percent"],
        "within_tolerance": within,
        "reconciliation": reconciliation,
        # 月度汇总权重：燃料输入热量
        "fuel_energy_weight": run["coal_flow_tph"] * coal["qnet_kj_kg"],
    }


def _calculate_if_ready(conn, shift_id: str) -> int | None:
    """运行数据与煤质齐备时算出新计算版本，返回版本号；否则返回 None。

    调用方须已持有该班次的咨询锁。
    """
    run = repo.latest_versioned(conn, "run_data", shift_id)
    coal = repo.latest_versioned(conn, "coal_quality", shift_id)
    if run is None or coal is None:
        return None
    config_version, config = repo.latest_config(conn)
    result = compute_shift_result(
        shift_id, run[1], coal[1], config, run[0], coal[0], config_version
    )
    return repo.insert_calculation(conn, shift_id, run[0], coal[0], config_version, result)


def submit_run_data(pool: ConnectionPool, shift_id: str, payload: dict) -> dict:
    with pool.connection() as conn, conn.transaction():
        repo.lock_shift(conn, shift_id)
        version = repo.insert_versioned(conn, "run_data", shift_id, payload)
        calc_version = _calculate_if_ready(conn, shift_id)
        if calc_version is not None:
            _recompute_month(conn, repo.month_of(shift_id))
        return {
            "shift_id": shift_id,
            "run_data_version": version,
            "calculation_version": calc_version,
            "status": "ok" if calc_version is not None else "pending_assay",
        }


def submit_coal_quality(pool: ConnectionPool, shift_id: str, payload: dict) -> dict:
    with pool.connection() as conn, conn.transaction():
        repo.lock_shift(conn, shift_id)
        version = repo.insert_versioned(conn, "coal_quality", shift_id, payload)
        calc_version = _calculate_if_ready(conn, shift_id)
        if calc_version is not None:
            _recompute_month(conn, repo.month_of(shift_id))
        return {
            "shift_id": shift_id,
            "coal_quality_version": version,
            "calculation_version": calc_version,
            "status": "ok" if calc_version is not None else "pending_run_data",
        }


def update_config(pool: ConnectionPool, payload: dict) -> dict:
    """写入新配置版本，并对所有数据齐备的班次产生新计算版本。"""
    with pool.connection() as conn, conn.transaction():
        version = repo.insert_config(conn, payload)
        shift_ids = repo.shifts_with_complete_data(conn)
        recalculated = []
        for shift_id in shift_ids:  # 已按 shift_id 排序，避免锁顺序不一致
            repo.lock_shift(conn, shift_id)
            calc_version = _calculate_if_ready(conn, shift_id)
            if calc_version is not None:
                recalculated.append(
                    {"shift_id": shift_id, "calculation_version": calc_version}
                )
        months = sorted({repo.month_of(s) for s in shift_ids})
        for month in months:
            _recompute_month(conn, month)
        return {
            "config_version": version,
            "recalculated_shifts": recalculated,
            "recomputed_months": months,
        }


# ---------------------------------------------------------------------------
# 月度汇总
# ---------------------------------------------------------------------------


def _summarize_month(month: str, calcs: list[dict]) -> dict:
    """由各班最新计算结果从头汇总月度值（纯函数）。"""
    shifts = []
    weight_total = 0.0
    direct_acc = 0.0
    indirect_acc = 0.0
    for c in calcs:
        result = c["result"]
        w = result["fuel_energy_weight"]
        weight_total += w
        direct_acc += w * result["direct"]["eta_percent"]
        indirect_acc += w * result["indirect"]["eta_percent"]
        shifts.append(
            {
                "shift_id": c["shift_id"],
                "calc_version": c["calc_version"],
                "run_data_version": result["run_data_version"],
                "coal_quality_version": result["coal_quality_version"],
                "config_version": result["config_version"],
                "weight": w,
                "eta_direct_percent": result["direct"]["eta_percent"],
                "eta_indirect_percent": result["indirect"]["eta_percent"],
            }
        )
    if weight_total > 0:
        eta_direct = direct_acc / weight_total
        eta_indirect = indirect_acc / weight_total
    else:
        eta_direct = eta_indirect = None
    return {
        "month": month,
        "shift_count": len(shifts),
        "total_weight": weight_total,
        "eta_direct_percent": eta_direct,
        "eta_indirect_percent": eta_indirect,
        "shifts": shifts,
    }


def _changed_shifts(old_payload: dict, new_payload: dict) -> list[dict]:
    """比较两版月度汇总，列出引起变化的班次。"""
    old = {s["shift_id"]: s for s in old_payload.get("shifts", [])}
    new = {s["shift_id"]: s for s in new_payload.get("shifts", [])}
    changed = []
    for shift_id in sorted(set(old) | set(new)):
        o, n = old.get(shift_id), new.get(shift_id)
        if o is None:
            changed.append({"shift_id": shift_id, "change": "added",
                            "new_calc_version": n["calc_version"]})
        elif n is None:
            changed.append({"shift_id": shift_id, "change": "removed",
                            "old_calc_version": o["calc_version"]})
        elif o["calc_version"] != n["calc_version"]:
            changed.append({
                "shift_id": shift_id,
                "change": "recalculated",
                "old_calc_version": o["calc_version"],
                "new_calc_version": n["calc_version"],
                "old_eta_direct_percent": o["eta_direct_percent"],
                "new_eta_direct_percent": n["eta_direct_percent"],
                "old_eta_indirect_percent": o["eta_indirect_percent"],
                "new_eta_indirect_percent": n["eta_indirect_percent"],
            })
    return changed


def _recompute_month(conn, month: str) -> int | None:
    """用最新计算版本重算月度草稿；有已发布值且结果变化时生成修订说明。"""
    repo.lock_month(conn, month)
    calcs = repo.latest_calculations_for_month(conn, month)
    if not calcs:
        return None
    payload = _summarize_month(month, calcs)

    latest = repo.latest_monthly(conn, month)
    if latest is not None and not latest[2] and latest[1] == payload:
        return latest[0]  # 草稿未变化，不产生新版本

    draft_version = repo.insert_monthly(conn, month, payload, published=False)

    published = repo.latest_monthly(conn, month, published=True)
    if published is not None:
        pub_version, pub_payload, _ = published
        if (
            pub_payload.get("eta_direct_percent") != payload["eta_direct_percent"]
            or pub_payload.get("eta_indirect_percent") != payload["eta_indirect_percent"]
        ):
            detail = {
                "month": month,
                "published": {
                    "version": pub_version,
                    "eta_direct_percent": pub_payload.get("eta_direct_percent"),
                    "eta_indirect_percent": pub_payload.get("eta_indirect_percent"),
                },
                "current": {
                    "version": draft_version,
                    "eta_direct_percent": payload["eta_direct_percent"],
                    "eta_indirect_percent": payload["eta_indirect_percent"],
                },
                "delta": {
                    "eta_direct_percent": payload["eta_direct_percent"]
                    - (pub_payload.get("eta_direct_percent") or 0.0),
                    "eta_indirect_percent": payload["eta_indirect_percent"]
                    - (pub_payload.get("eta_indirect_percent") or 0.0),
                },
                "changed_shifts": _changed_shifts(pub_payload, payload),
            }
            repo.insert_monthly_revision(conn, month, pub_version, draft_version, detail)
    return draft_version


def publish_month(pool: ConnectionPool, month: str) -> dict:
    """把当前草稿发布为不可变的正式月度值。"""
    with pool.connection() as conn, conn.transaction():
        repo.lock_month(conn, month)
        draft = repo.latest_monthly(conn, month, published=False)
        if draft is None:
            raise LookupError(f"{month} 没有可发布的月度草稿")
        version = repo.insert_monthly(conn, month, draft[1], published=True)
        return {"month": month, "published_version": version, "payload": draft[1]}


def get_monthly(pool: ConnectionPool, month: str) -> dict:
    with pool.connection() as conn:
        published = repo.latest_monthly(conn, month, published=True)
        draft = repo.latest_monthly(conn, month, published=False)
        if published is None and draft is None:
            raise LookupError(f"{month} 没有月度数据")
        revisions = repo.list_monthly_revisions(conn, month)
        return {
            "month": month,
            "published": (
                {"version": published[0], "payload": published[1]} if published else None
            ),
            "draft": ({"version": draft[0], "payload": draft[1]} if draft else None),
            "revision_count": len(revisions),
        }
