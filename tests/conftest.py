"""pytest 公共夹具。

集成测试需要 PostgreSQL 16，按以下顺序寻找：
  1. 环境变量 BOILER_TEST_DSN 指向的现成数据库（如 compose 起的 postgres:16）；
  2. 环境变量 BOILER_TEST_PG_BIN 指向的 PostgreSQL 二进制目录
     （含 initdb/pg_ctl，可用 scripts/start_test_db.sh 自动下载）；
  3. 都没有则跳过集成测试（纯单元测试不受影响）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import create_pool, init_schema  # noqa: E402
from app.schemas import DEFAULT_CONFIG  # noqa: E402

ALL_TABLES = (
    "monthly_revision",
    "monthly",
    "calculation",
    "config",
    "coal_quality",
    "run_data",
)


def _wait_ready(dsn: str, timeout: float = 30.0) -> None:
    import psycopg

    deadline = time.time() + timeout
    while True:
        try:
            with psycopg.connect(dsn):
                return
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.3)


@pytest.fixture(scope="session")
def pg_dsn(tmp_path_factory):
    dsn = os.environ.get("BOILER_TEST_DSN")
    if dsn:
        yield dsn
        return

    pg_bin = os.environ.get("BOILER_TEST_PG_BIN")
    if not pg_bin:
        pytest.skip("未配置 BOILER_TEST_DSN / BOILER_TEST_PG_BIN，跳过数据库集成测试")

    pg_bin = Path(pg_bin).resolve()
    pg_lib = pg_bin.parent / "lib"
    data_dir = tmp_path_factory.mktemp("pgdata")
    port = 55443
    sock_dir = tmp_path_factory.mktemp("pgsock")
    log_file = tmp_path_factory.mktemp("pglog") / "pg.log"
    env = {**os.environ, "LD_LIBRARY_PATH": str(pg_lib)}

    subprocess.run(
        [str(pg_bin / "initdb"), "-D", str(data_dir), "-U", "postgres",
         "--auth=trust", "-E", "UTF8"],
        check=True, env=env, capture_output=True,
    )
    subprocess.run(
        [str(pg_bin / "pg_ctl"), "-D", str(data_dir),
         "-o", f"-p {port} -k {sock_dir}", "-l", str(log_file), "start"],
        check=True, env=env, capture_output=True,
    )
    dsn = f"host={sock_dir} port={port} user=postgres dbname=postgres"
    try:
        _wait_ready(dsn)
        yield dsn
    finally:
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(data_dir), "-m", "fast", "stop"],
            env=env, capture_output=True,
        )


@pytest.fixture()
def pool(pg_dsn):
    pool = create_pool(pg_dsn, retries=3)
    with pool.connection() as conn:
        for table in ALL_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        conn.commit()
    init_schema(pool, seed_config=DEFAULT_CONFIG)
    yield pool
    pool.close()


@pytest.fixture()
def client(pool):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(pool)) as c:
        yield c
