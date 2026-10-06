"""版本化存储：运行数据、煤质、配置、计算结果、月度汇总。

版本与并发约定：
  * 运行数据、煤质、配置各自按 (班次, 递增版本号) 或 (递增版本号) 追加，永不改写；
  * 任何输入版本变化都会在同一事务内触发重算，产生新的结果版本，
    结果记录其所依据的运行数据版本、煤质版本与配置版本；
  * 会触发重算的写操作都先对 shifts 行 SELECT ... FOR UPDATE 加锁，
    同一班次的并发写入因此串行化：后提交的事务能看到先提交事务的全部输入，
    最新结果版本不会停留在只反映部分输入的中间状态；
  * 月度汇总在每次重算后由当前全部最新结果从头汇总，天然与重算口径一致；
    已发布月份不改写发布值，只追加修订说明。
"""
from __future__ import annotations

import math
from datetime import date

from psycopg.types.json import Jsonb

from . import engine
from .config import CalcConfig
from .schemas import CoalQuality, RunData


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


# ---------------------------------------------------------------- 班次与输入版本


def lock_shift(conn, shift_id: str, shift_date: date | None = None) -> date | None:
    """建立（如需要）并锁定班次行，同事务内后续写入串行化。

    运行数据是班次日期的权威来源：传入日期时以其为准更新。
    返回更新前的班次日期（用于班次跨月改正时刷新原月份）。
    """
    conn.execute(
        "INSERT INTO shifts(shift_id, shift_date) VALUES (%s, %s) "
        "ON CONFLICT (shift_id) DO NOTHING",
        (shift_id, shift_date),
    )
    row = conn.execute(
        "SELECT shift_date FROM shifts WHERE shift_id = %s FOR UPDATE", (shift_id,)
    ).fetchone()
    old_date = row["shift_date"]
    if shift_date is not None and shift_date != old_date:
        conn.execute(
            "UPDATE shifts SET shift_date = %s WHERE shift_id = %s",
            (shift_date, shift_id),
        )
    return old_date


def _next_version(conn, table: str, shift_id: str) -> int:
    row = conn.execute(
        f"SELECT COALESCE(MAX(version), 0) + 1 AS v FROM {table} WHERE shift_id = %s",
        (shift_id,),
    ).fetchone()
    return row["v"]


def insert_run_data(conn, shift_id: str, run: RunData) -> int:
    version = _next_version(conn, "run_data_versions", shift_id)
    conn.execute(
        "INSERT INTO run_data_versions(shift_id, version, payload) VALUES (%s, %s, %s)",
        (shift_id, version, Jsonb(run.model_dump(mode="json"))),
    )
    return version


def insert_coal_quality(conn, shift_id: str, coal: CoalQuality) -> int:
    version = _next_version(conn, "coal_quality_versions", shift_id)
    conn.execute(
        "INSERT INTO coal_quality_versions(shift_id, version, payload) VALUES (%s, %s, %s)",
        (shift_id, version, Jsonb(coal.model_dump(mode="json"))),
    )
    return version


def _latest(conn, table: str, shift_id: str) -> dict | None:
    return conn.execute(
        f"SELECT version, payload FROM {table} WHERE shift_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (shift_id,),
    ).fetchone()


def latest_run_data(conn, shift_id: str) -> dict | None:
    return _latest(conn, "run_data_versions", shift_id)


def latest_coal_quality(conn, shift_id: str) -> dict | None:
    return _latest(conn, "coal_quality_versions", shift_id)


# ---------------------------------------------------------------- 配置版本


def current_config(conn) -> tuple[int, dict]:
    row = conn.execute(
        "SELECT version, payload FROM config_versions ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:  # pragma: no cover - 启动时保证有默认配置
        raise NotFoundError("配置不存在")
    return row["version"], row["payload"]


def insert_config(conn, config: CalcConfig) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM config_versions"
    ).fetchone()
    version = row["v"]
    conn.execute(
        "INSERT INTO config_versions(version, payload) VALUES (%s, %s)",
        (version, Jsonb(config.model_dump())),
    )
    return version


# ---------------------------------------------------------------- 结果版本


def insert_result(
    conn,
    shift_id: str,
    run_data_version: int,
    coal_quality_version: int | None,
    config_version: int,
    result: dict,
) -> int:
    version = _next_version(conn, "results", shift_id)
    direct = result.get("direct") or {}
    indirect = result.get("indirect") or {}
    conn.execute(
        "INSERT INTO results(shift_id, version, run_data_version, coal_quality_version,"
        " config_version, status, output_heat, direct_efficiency, indirect_efficiency, result)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            shift_id,
            version,
            run_data_version,
            coal_quality_version,
            config_version,
            result["status"],
            direct.get("output_heat"),
            direct.get("efficiency"),
            indirect.get("efficiency"),
            Jsonb(result),
        ),
    )
    return version


def recompute_shift(conn, shift_id: str) -> int | None:
    """按当前最新输入与配置重算一个班次，写入新结果版本并刷新月度汇总。"""
    run_row = latest_run_data(conn, shift_id)
    if run_row is None:
        return None  # 运行数据未到，无法产生任何结果
    coal_row = latest_coal_quality(conn, shift_id)
    config_version, config_payload = current_config(conn)

    run = RunData(**run_row["payload"])
    coal = CoalQuality(**coal_row["payload"]) if coal_row else None
    result = engine.compute_shift_result(run, coal, CalcConfig(**config_payload))

    version = insert_result(
        conn,
        shift_id,
        run_row["version"],
        coal_row["version"] if coal_row else None,
        config_version,
        result,
    )

    row = conn.execute(
        "SELECT shift_date FROM shifts WHERE shift_id = %s", (shift_id,)
    ).fetchone()
    if row and row["shift_date"]:
        d = row["shift_date"]
        refresh_monthly(conn, f"{d.year:04d}-{d.month:02d}")
    return version


def recompute_all_shifts(conn) -> int:
    """配置变更后重算全部班次。按 shift_id 排序加锁，避免与班次写操作死锁。"""
    rows = conn.execute("SELECT shift_id FROM shifts ORDER BY shift_id FOR UPDATE").fetchall()
    count = 0
    for row in rows:
        if recompute_shift(conn, row["shift_id"]) is not None:
            count += 1
    return count


def get_result(conn, shift_id: str, version: int | None = None) -> dict:
    if version is None:
        row = conn.execute(
            "SELECT * FROM results WHERE shift_id = %s ORDER BY version DESC LIMIT 1",
            (shift_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM results WHERE shift_id = %s AND version = %s",
            (shift_id, version),
        ).fetchone()
    if row is None:
        raise NotFoundError(f"班次 {shift_id} 不存在该结果版本")
    return row


def list_results(conn, shift_id: str) -> list[dict]:
    return conn.execute(
        "SELECT version, run_data_version, coal_quality_version, config_version,"
        " status, direct_efficiency, indirect_efficiency, created_at"
        " FROM results WHERE shift_id = %s ORDER BY version",
        (shift_id,),
    ).fetchall()


# ---------------------------------------------------------------- 月度汇总


def _month_range(month: str) -> tuple[date, date]:
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    end = date(year + (1 if mon == 12 else 0), mon % 12 + 1, 1)
    return start, end


def _latest_computed_results(conn, month: str) -> list[dict]:
    start, end = _month_range(month)
    return conn.execute(
        "SELECT r.shift_id, r.version, r.direct_efficiency, r.indirect_efficiency,"
        " r.output_heat"
        " FROM results r JOIN shifts s ON s.shift_id = r.shift_id"
        " WHERE s.shift_date >= %s AND s.shift_date < %s AND r.status = 'computed'"
        " AND r.version = (SELECT MAX(version) FROM results r2"
        "                  WHERE r2.shift_id = r.shift_id)"
        " ORDER BY r.shift_id",
        (start, end),
    ).fetchall()


def _aggregate(rows: list[dict]) -> tuple[float, float, float]:
    """按班次输出热量加权汇总。用 math.fsum 保证与任何从头汇总的结果逐位一致。"""
    total_weight = math.fsum(r["output_heat"] for r in rows)
    direct = math.fsum(r["output_heat"] * r["direct_efficiency"] for r in rows) / total_weight
    indirect = (
        math.fsum(r["output_heat"] * r["indirect_efficiency"] for r in rows) / total_weight
    )
    return direct, indirect, total_weight


def _changed_shifts(conn, month: str, rows: list[dict]) -> list[dict]:
    snapshot = {
        r["shift_id"]: r
        for r in conn.execute(
            "SELECT shift_id, result_version, direct_efficiency, indirect_efficiency"
            " FROM monthly_snapshots WHERE month = %s",
            (month,),
        ).fetchall()
    }
    changed: list[dict] = []
    current_ids = set()
    for row in rows:
        sid = row["shift_id"]
        current_ids.add(sid)
        new = {
            "result_version": row["version"],
            "direct_efficiency": row["direct_efficiency"],
            "indirect_efficiency": row["indirect_efficiency"],
        }
        old_row = snapshot.get(sid)
        if old_row is None:
            changed.append({"shift_id": sid, "old": None, "new": new})
        elif (
            old_row["result_version"] != row["version"]
            or old_row["direct_efficiency"] != row["direct_efficiency"]
            or old_row["indirect_efficiency"] != row["indirect_efficiency"]
        ):
            old = {
                "result_version": old_row["result_version"],
                "direct_efficiency": old_row["direct_efficiency"],
                "indirect_efficiency": old_row["indirect_efficiency"],
            }
            changed.append({"shift_id": sid, "old": old, "new": new})
    for sid, old_row in snapshot.items():
        if sid not in current_ids:
            changed.append(
                {
                    "shift_id": sid,
                    "old": {
                        "result_version": old_row["result_version"],
                        "direct_efficiency": old_row["direct_efficiency"],
                        "indirect_efficiency": old_row["indirect_efficiency"],
                    },
                    "new": None,
                }
            )
    return changed


def refresh_monthly(conn, month: str) -> None:
    """由当前全部最新结果从头汇总月度值；已发布月份不改写发布值，只追加修订说明。"""
    rows = _latest_computed_results(conn, month)
    existing = conn.execute(
        "SELECT * FROM monthly WHERE month = %s", (month,)
    ).fetchone()

    if not rows:
        if existing and not existing["published"]:
            conn.execute("DELETE FROM monthly WHERE month = %s", (month,))
        return

    direct, indirect, total_weight = _aggregate(rows)

    if existing and existing["published"]:
        if (
            direct != existing["published_direct"]
            or indirect != existing["published_indirect"]
        ):
            changed = _changed_shifts(conn, month, rows)
            conn.execute(
                "INSERT INTO monthly_revisions(month, old_direct, old_indirect,"
                " new_direct, new_indirect, changed_shifts)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    month,
                    existing["published_direct"],
                    existing["published_indirect"],
                    direct,
                    indirect,
                    Jsonb(changed),
                ),
            )
        conn.execute(
            "UPDATE monthly SET shift_count = %s, total_weight = %s,"
            " direct_efficiency = %s, indirect_efficiency = %s, updated_at = now()"
            " WHERE month = %s",
            (len(rows), total_weight, direct, indirect, month),
        )
    else:
        conn.execute(
            "INSERT INTO monthly(month, shift_count, total_weight,"
            " direct_efficiency, indirect_efficiency)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (month) DO UPDATE SET"
            " shift_count = EXCLUDED.shift_count, total_weight = EXCLUDED.total_weight,"
            " direct_efficiency = EXCLUDED.direct_efficiency,"
            " indirect_efficiency = EXCLUDED.indirect_efficiency, updated_at = now()",
            (month, len(rows), total_weight, direct, indirect),
        )


def publish_monthly(conn, month: str) -> None:
    row = conn.execute(
        "SELECT published FROM monthly WHERE month = %s FOR UPDATE", (month,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"{month} 无月度数据")
    if row["published"]:
        raise ConflictError(f"{month} 已发布，不能重复发布")
    conn.execute(
        "UPDATE monthly SET published = TRUE, published_direct = direct_efficiency,"
        " published_indirect = indirect_efficiency, published_at = now()"
        " WHERE month = %s",
        (month,),
    )
    conn.execute("DELETE FROM monthly_snapshots WHERE month = %s", (month,))
    for row in _latest_computed_results(conn, month):
        conn.execute(
            "INSERT INTO monthly_snapshots(month, shift_id, result_version,"
            " direct_efficiency, indirect_efficiency) VALUES (%s, %s, %s, %s, %s)",
            (month, row["shift_id"], row["version"], row["direct_efficiency"],
             row["indirect_efficiency"]),
        )


def get_monthly(conn, month: str) -> dict:
    row = conn.execute("SELECT * FROM monthly WHERE month = %s", (month,)).fetchone()
    if row is None:
        raise NotFoundError(f"{month} 无月度数据")
    return row


def list_revisions(conn, month: str) -> list[dict]:
    return conn.execute(
        "SELECT * FROM monthly_revisions WHERE month = %s ORDER BY id", (month,)
    ).fetchall()
