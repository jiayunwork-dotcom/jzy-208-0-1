"""并发到达不产生中间版本的集成测试（需要 PostgreSQL）。

化验结果与运行数据改正同时到达同一班次：两个请求并发提交，
最终最新计算版本必须同时基于双方的新版本，而不是只反映其中一方。
"""
import threading
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import repository as repo
from app.service import submit_coal_quality, submit_run_data

from .fixtures import make_coal, make_run

SHIFT = "2026-10-06-A"


def _latest_calc(pool, shift_id):
    with pool.connection() as conn:
        return repo.latest_calculation(conn, shift_id)


def test_concurrent_arrival_no_intermediate_latest(pool):
    # 先让班次达到可计算状态（v1 + v1）
    submit_run_data(pool, SHIFT, make_run())
    submit_coal_quality(pool, SHIFT, make_coal())
    version, result = _latest_calc(pool, SHIFT)
    assert version == 1

    new_run = make_run(coal_flow_tph=155.0)
    new_coal = make_coal(carbon_percent=54.0, ash_percent=22.7)

    barrier = threading.Barrier(2)
    errors = []

    def post_run():
        try:
            barrier.wait(timeout=10)
            submit_run_data(pool, SHIFT, new_run)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def post_coal():
        try:
            barrier.wait(timeout=10)
            submit_coal_quality(pool, SHIFT, new_coal)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=post_run), threading.Thread(target=post_coal)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors

    # 最新计算版本必须同时基于运行数据 v2 与煤质 v2
    version, result = _latest_calc(pool, SHIFT)
    assert result["run_data_version"] == 2
    assert result["coal_quality_version"] == 2
    # 中间版本（若产生过）只能作为历史存在，且两个方向各算过一次
    assert version >= 2

    # 历史版本链完整可取
    with pool.connection() as conn:
        for v in range(1, version + 1):
            assert repo.get_calculation(conn, SHIFT, v) is not None


def test_concurrent_same_kind_writes_keep_version_chain(pool):
    """同一类数据的并发改正：版本号不重不漏。"""
    submit_coal_quality(pool, SHIFT, make_coal())
    barrier = threading.Barrier(4)
    errors = []

    def post(i):
        try:
            barrier.wait(timeout=10)
            submit_run_data(pool, SHIFT, make_run(coal_flow_tph=140.0 + i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors

    with pool.connection() as conn:
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM run_data WHERE shift_id = %s ORDER BY version",
                (SHIFT,),
            ).fetchall()
        ]
    assert versions == [1, 2, 3, 4]
