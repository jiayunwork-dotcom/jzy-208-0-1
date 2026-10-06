"""数据库连接与建表。持久层为 PostgreSQL 16。"""
from __future__ import annotations

import os
import time

from psycopg_pool import ConnectionPool

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/boiler"

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS run_data (
        shift_id   text        NOT NULL,
        version    integer     NOT NULL,
        payload    jsonb       NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (shift_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coal_quality (
        shift_id   text        NOT NULL,
        version    integer     NOT NULL,
        payload    jsonb       NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (shift_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config (
        version    integer     PRIMARY KEY,
        payload    jsonb       NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calculation (
        shift_id             text        NOT NULL,
        version              integer     NOT NULL,
        run_data_version     integer     NOT NULL,
        coal_quality_version integer     NOT NULL,
        config_version       integer     NOT NULL,
        result               jsonb       NOT NULL,
        created_at           timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (shift_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monthly (
        month      text        NOT NULL,
        version    integer     NOT NULL,
        payload    jsonb       NOT NULL,
        published  boolean     NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (month, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monthly_revision (
        id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        month             text        NOT NULL,
        published_version integer     NOT NULL,
        draft_version     integer     NOT NULL,
        detail            jsonb       NOT NULL,
        created_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
]


def create_pool(dsn: str | None = None, retries: int = 10, **kwargs) -> ConnectionPool:
    """创建连接池；数据库未就绪时按 1s 间隔重试（容器启动竞态）。"""
    dsn = dsn or os.environ.get("DATABASE_URL", DEFAULT_DSN)
    kwargs.setdefault("min_size", 1)
    kwargs.setdefault("max_size", 10)
    kwargs.setdefault("open", True)
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            pool = ConnectionPool(dsn, **kwargs)
            pool.wait(timeout=10)
            return pool
        except Exception as exc:  # noqa: BLE001 - 启动期重试
            last_exc = exc
            time.sleep(1)
    raise RuntimeError(f"无法连接数据库 {dsn}: {last_exc}")


def init_schema(pool: ConnectionPool, seed_config: dict | None = None) -> None:
    """建表，并在 config 表为空时写入默认配置（版本 1）。"""
    from psycopg.types.json import Jsonb

    with pool.connection() as conn:
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        if seed_config is not None:
            conn.execute(
                """
                INSERT INTO config (version, payload)
                SELECT 1, %s
                WHERE NOT EXISTS (SELECT 1 FROM config)
                """,
                (Jsonb(seed_config),),
            )
        conn.commit()
