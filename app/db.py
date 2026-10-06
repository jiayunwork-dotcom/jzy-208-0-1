"""数据库连接、建表与启动初始化。"""
from __future__ import annotations

import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shifts (
    shift_id    TEXT PRIMARY KEY,
    shift_date  DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS run_data_versions (
    shift_id   TEXT NOT NULL REFERENCES shifts(shift_id),
    version    INT  NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (shift_id, version)
);
CREATE TABLE IF NOT EXISTS coal_quality_versions (
    shift_id   TEXT NOT NULL REFERENCES shifts(shift_id),
    version    INT  NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (shift_id, version)
);
CREATE TABLE IF NOT EXISTS config_versions (
    version    INT PRIMARY KEY,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS results (
    shift_id              TEXT NOT NULL REFERENCES shifts(shift_id),
    version               INT  NOT NULL,
    run_data_version      INT  NOT NULL,
    coal_quality_version  INT,
    config_version        INT  NOT NULL,
    status                TEXT NOT NULL,           -- pending_assay | computed
    output_heat           DOUBLE PRECISION,
    direct_efficiency     DOUBLE PRECISION,
    indirect_efficiency   DOUBLE PRECISION,
    result                JSONB NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (shift_id, version)
);
CREATE TABLE IF NOT EXISTS monthly (
    month               TEXT PRIMARY KEY,          -- YYYY-MM
    shift_count         INT NOT NULL,
    total_weight        DOUBLE PRECISION NOT NULL,
    direct_efficiency   DOUBLE PRECISION NOT NULL,
    indirect_efficiency DOUBLE PRECISION NOT NULL,
    published           BOOLEAN NOT NULL DEFAULT FALSE,
    published_direct    DOUBLE PRECISION,
    published_indirect  DOUBLE PRECISION,
    published_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS monthly_snapshots (
    month               TEXT NOT NULL,
    shift_id            TEXT NOT NULL,
    result_version      INT  NOT NULL,
    direct_efficiency   DOUBLE PRECISION NOT NULL,
    indirect_efficiency DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (month, shift_id)
);
CREATE TABLE IF NOT EXISTS monthly_revisions (
    id             SERIAL PRIMARY KEY,
    month          TEXT NOT NULL,
    old_direct     DOUBLE PRECISION NOT NULL,
    old_indirect   DOUBLE PRECISION NOT NULL,
    new_direct     DOUBLE PRECISION NOT NULL,
    new_indirect   DOUBLE PRECISION NOT NULL,
    changed_shifts JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

ALL_TABLES = [
    "monthly_revisions",
    "monthly_snapshots",
    "monthly",
    "results",
    "config_versions",
    "coal_quality_versions",
    "run_data_versions",
    "shifts",
]


def create_pool(database_url: str) -> ConnectionPool:
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def init_schema(conn) -> None:
    conn.execute(SCHEMA_SQL)


def ensure_default_config(conn) -> None:
    from .config import CalcConfig

    row = conn.execute("SELECT COUNT(*) AS n FROM config_versions").fetchone()
    if row["n"] == 0:
        conn.execute(
            "INSERT INTO config_versions(version, payload) VALUES (1, %s)",
            (Jsonb(CalcConfig().model_dump()),),
        )


def reset_database(conn) -> None:
    """清空全部数据并重建默认配置（测试用）。"""
    for table in ALL_TABLES:
        conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    ensure_default_config(conn)


def wait_and_init(database_url: str, attempts: int = 30) -> None:
    """等数据库可用后建表并写入默认配置（容器启动时数据库可能尚未就绪）。"""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(database_url, row_factory=dict_row) as conn:
                init_schema(conn)
                ensure_default_config(conn)
            return
        except psycopg.OperationalError as exc:  # pragma: no cover - 依赖外部环境
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"数据库不可用: {last_error}")
