"""pytest 公共装置：嵌入式 PostgreSQL 16、样例数据、客户端。

集成测试用 conda-forge 的 PostgreSQL 16 二进制（/workspace/.pg，可用 PG_BIN 覆盖），
每个测试函数前清空数据库并重建默认配置。
"""
from __future__ import annotations

import os
import socket
import subprocess
import time

import pytest

PG_BIN = os.environ.get("PG_BIN", "/workspace/.pg/bin")

# 元素和 = 100.0
SAMPLE_COAL = {
    "carbon": 58.0,
    "hydrogen": 3.6,
    "oxygen": 6.5,
    "nitrogen": 1.0,
    "sulfur": 0.8,
    "moisture": 9.0,
    "ash": 21.1,
    "qnet": 21500.0,
}

SAMPLE_RUN = {
    "shift_date": "2026-10-05",
    "coal_flow": 135000.0,
    "main_steam_flow": 1_000_000.0,
    "main_steam_enthalpy": 3400.0,
    "feedwater_enthalpy": 1150.0,
    "rh_steam_flow": 850_000.0,
    "rh_inlet_enthalpy": 3050.0,
    "rh_outlet_enthalpy": 3550.0,
    "blowdown_flow": 5000.0,
    "flue_gas_temp": 140.0,
    "ambient_temp": 25.0,
    "flue_o2": 5.5,
    "flue_co": 0.01,
    "fly_ash_carbon": 2.0,
    "slag_carbon": 6.0,
    "fly_ash_share": 0.9,
    "slag_share": 0.1,
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    pgdata = tmp_path_factory.mktemp("pgdata")
    sockdir = tmp_path_factory.mktemp("pgsock")
    port = _free_port()
    subprocess.run(
        [f"{PG_BIN}/initdb", "-D", str(pgdata), "-U", "postgres",
         "--auth=trust", "-E", "UTF8", "--locale=C"],
        check=True, capture_output=True,
    )
    proc = subprocess.Popen(
        [f"{PG_BIN}/postgres", "-D", str(pgdata), "-p", str(port),
         "-k", str(sockdir), "-h", "127.0.0.1",
         "-c", "shared_buffers=32MB", "-c", "max_connections=50"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    import psycopg

    for _ in range(120):
        try:
            psycopg.connect(url, connect_timeout=1).close()
            break
        except psycopg.OperationalError:
            time.sleep(0.25)
    else:  # pragma: no cover
        proc.kill()
        raise RuntimeError("PostgreSQL 未能启动")
    yield url
    proc.terminate()
    proc.wait(timeout=15)


@pytest.fixture()
def client(database_url):
    import psycopg
    from fastapi.testclient import TestClient
    from psycopg.rows import dict_row

    from app.db import init_schema, reset_database
    from app.main import create_app

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        init_schema(conn)
        reset_database(conn)

    app = create_app(database_url)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def coal_payload():
    return dict(SAMPLE_COAL)


@pytest.fixture()
def run_payload():
    return dict(SAMPLE_RUN)


def make_consistent(run: dict, coal: dict):
    """调整入炉煤量使正平衡与反平衡完全一致（测试辅助）。"""
    from app.config import CalcConfig
    from app import direct, indirect
    from app.schemas import CoalQuality, RunData

    config = CalcConfig()
    run_model = RunData(**run)
    coal_model = CoalQuality(**coal)
    indirect_eff = indirect.compute(run_model, coal_model, config).efficiency
    output_heat = direct.compute(run_model, coal_model, config).output_heat
    run = dict(run)
    run["coal_flow"] = output_heat / (indirect_eff / 100.0 * coal["qnet"])
    return run
