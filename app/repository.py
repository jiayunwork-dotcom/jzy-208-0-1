"""版本库：所有业务数据的版本化存取。

设计要点
--------
* 每类数据一张表，(业务键, version) 为主键，版本号从 1 起按业务键单调递增，
  旧版本永不改写、永不删除，可按版本号取回。
* 同一班次的并发写入用 PostgreSQL 咨询锁（pg_advisory_xact_lock）串行化：
  锁在事务内先取，再读"最新版本"、再插入，保证不会出现基于过期快照的
  中间结果被当作最新版本提交。
* 月度表同样版本化；published=true 的行发布后不可变，后续变化只产生新的
  草稿版本与修订说明（monthly_revision）。
"""
from __future__ import annotations

import re

from psycopg import Connection
from psycopg.types.json import Jsonb

# 锁命名空间：1=班次，2=月份。不同命名空间的锁互不干扰。
_LOCK_NS_SHIFT = 1
_LOCK_NS_MONTH = 2

SHIFT_ID_RE = re.compile(r"^(\d{4}-\d{2})-\d{2}-[A-Za-z0-9_]+$")

_VERSIONED_TABLES = {"run_data", "coal_quality"}


def month_of(shift_id: str) -> str:
    """从班次号（YYYY-MM-DD-xxx）推出所属月份 YYYY-MM。"""
    m = SHIFT_ID_RE.match(shift_id)
    if not m:
        raise ValueError(
            f"shift_id 格式应为 YYYY-MM-DD-<班别>，例如 2026-10-06-A，收到 {shift_id!r}"
        )
    return m.group(1)


def lock_shift(conn: Connection, shift_id: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (_LOCK_NS_SHIFT, shift_id))


def lock_month(conn: Connection, month: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (_LOCK_NS_MONTH, month))


# ---------------------------------------------------------------------------
# 通用版本表（run_data / coal_quality）
# ---------------------------------------------------------------------------


def insert_versioned(conn: Connection, table: str, shift_id: str, payload: dict) -> int:
    """插入新版本并返回版本号。调用方须已持有该班次的咨询锁。"""
    assert table in _VERSIONED_TABLES
    row = conn.execute(
        f"SELECT COALESCE(MAX(version), 0) + 1 FROM {table} WHERE shift_id = %s",
        (shift_id,),
    ).fetchone()
    version = row[0]
    conn.execute(
        f"INSERT INTO {table} (shift_id, version, payload) VALUES (%s, %s, %s)",
        (shift_id, version, Jsonb(payload)),
    )
    return version


def latest_versioned(conn: Connection, table: str, shift_id: str) -> tuple[int, dict] | None:
    assert table in _VERSIONED_TABLES
    row = conn.execute(
        f"SELECT version, payload FROM {table} WHERE shift_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (shift_id,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def get_versioned(conn: Connection, table: str, shift_id: str, version: int) -> dict | None:
    assert table in _VERSIONED_TABLES
    row = conn.execute(
        f"SELECT payload FROM {table} WHERE shift_id = %s AND version = %s",
        (shift_id, version),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def insert_config(conn: Connection, payload: dict) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM config").fetchone()
    version = row[0]
    conn.execute(
        "INSERT INTO config (version, payload) VALUES (%s, %s)",
        (version, Jsonb(payload)),
    )
    return version


def latest_config(conn: Connection) -> tuple[int, dict]:
    row = conn.execute(
        "SELECT version, payload FROM config ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise LookupError("配置不存在：请先 POST /config 或确认默认配置已初始化")
    return row[0], row[1]


# ---------------------------------------------------------------------------
# 计算结果
# ---------------------------------------------------------------------------


def insert_calculation(
    conn: Connection,
    shift_id: str,
    run_data_version: int,
    coal_quality_version: int,
    config_version: int,
    result: dict,
) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM calculation WHERE shift_id = %s",
        (shift_id,),
    ).fetchone()
    version = row[0]
    conn.execute(
        """
        INSERT INTO calculation
            (shift_id, version, run_data_version, coal_quality_version,
             config_version, result)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (shift_id, version, run_data_version, coal_quality_version, config_version, Jsonb(result)),
    )
    return version


def latest_calculation(conn: Connection, shift_id: str) -> tuple[int, dict] | None:
    row = conn.execute(
        "SELECT version, result FROM calculation WHERE shift_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (shift_id,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def get_calculation(conn: Connection, shift_id: str, version: int) -> dict | None:
    row = conn.execute(
        "SELECT result FROM calculation WHERE shift_id = %s AND version = %s",
        (shift_id, version),
    ).fetchone()
    return row[0] if row else None


def latest_calculations_for_month(conn: Connection, month: str) -> list[dict]:
    """某月全部班次的最新计算版本（每班一行）。"""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (shift_id) shift_id, version, result
        FROM calculation
        WHERE shift_id LIKE %s
        ORDER BY shift_id, version DESC
        """,
        (month + "-%",),
    ).fetchall()
    return [
        {"shift_id": r[0], "calc_version": r[1], "result": r[2]} for r in rows
    ]


def shifts_with_complete_data(conn: Connection) -> list[str]:
    """运行数据与煤质都已具备的班次（用于配置变更后的全量重算）。"""
    rows = conn.execute(
        """
        SELECT r.shift_id
        FROM (SELECT DISTINCT shift_id FROM run_data) r
        JOIN (SELECT DISTINCT shift_id FROM coal_quality) c USING (shift_id)
        ORDER BY r.shift_id
        """
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# 月度汇总
# ---------------------------------------------------------------------------


def insert_monthly(conn: Connection, month: str, payload: dict, published: bool) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM monthly WHERE month = %s",
        (month,),
    ).fetchone()
    version = row[0]
    conn.execute(
        "INSERT INTO monthly (month, version, payload, published) VALUES (%s, %s, %s, %s)",
        (month, version, Jsonb(payload), published),
    )
    return version


def latest_monthly(
    conn: Connection, month: str, published: bool | None = None
) -> tuple[int, dict, bool] | None:
    if published is None:
        row = conn.execute(
            "SELECT version, payload, published FROM monthly WHERE month = %s "
            "ORDER BY version DESC LIMIT 1",
            (month,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT version, payload, published FROM monthly WHERE month = %s AND published = %s "
            "ORDER BY version DESC LIMIT 1",
            (month, published),
        ).fetchone()
    return (row[0], row[1], row[2]) if row else None


def insert_monthly_revision(
    conn: Connection,
    month: str,
    published_version: int,
    draft_version: int,
    detail: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO monthly_revision (month, published_version, draft_version, detail)
        VALUES (%s, %s, %s, %s)
        """,
        (month, published_version, draft_version, Jsonb(detail)),
    )


def list_monthly_revisions(conn: Connection, month: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, published_version, draft_version, detail, created_at
        FROM monthly_revision WHERE month = %s ORDER BY id
        """,
        (month,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "published_version": r[1],
            "draft_version": r[2],
            "detail": r[3],
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]
