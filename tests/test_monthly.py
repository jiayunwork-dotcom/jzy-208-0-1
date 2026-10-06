"""月度汇总与已发布值修订的集成测试（需要 PostgreSQL）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import repository as repo
from app.service import _summarize_month

from .fixtures import make_coal, make_run

MONTH = "2026-10"
SHIFTS = ["2026-10-06-A", "2026-10-06-B", "2026-10-07-A"]


def _setup_three_shifts(client):
    runs = [
        make_run(coal_flow_tph=140.0),
        make_run(coal_flow_tph=145.0),
        make_run(coal_flow_tph=150.0),
    ]
    for shift, run in zip(SHIFTS, runs):
        assert client.post(f"/shifts/{shift}/run-data", json=run).status_code == 201
        assert client.post(f"/shifts/{shift}/coal-quality", json=make_coal()).status_code == 201


def _from_scratch(pool, month):
    """独立地从头汇总：直接查库取各班最新计算版本再加权。"""
    with pool.connection() as conn:
        calcs = repo.latest_calculations_for_month(conn, month)
    return _summarize_month(month, calcs)


def test_monthly_matches_from_scratch_aggregation(client, pool):
    """月度值必须与用当前所有最新版本从头汇总的结果完全一致。"""
    _setup_three_shifts(client)
    r = client.get(f"/monthly/{MONTH}")
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]["payload"]
    assert draft["shift_count"] == 3

    expected = _from_scratch(pool, MONTH)
    assert draft["eta_direct_percent"] == pytest.approx(expected["eta_direct_percent"])
    assert draft["eta_indirect_percent"] == pytest.approx(expected["eta_indirect_percent"])

    # 权重校验：燃料输入热量加权
    weights = [140.0 * 21000.0, 145.0 * 21000.0, 150.0 * 21000.0]
    assert draft["total_weight"] == pytest.approx(sum(weights))

    # 某班数据改正后，月度值跟着变，且仍与从头汇总一致
    client.post(f"/shifts/{SHIFTS[0]}/run-data", json=make_run(coal_flow_tph=160.0))
    r = client.get(f"/monthly/{MONTH}")
    draft2 = r.json()["draft"]["payload"]
    expected2 = _from_scratch(pool, MONTH)
    assert draft2["eta_direct_percent"] == pytest.approx(expected2["eta_direct_percent"])
    assert draft2["eta_indirect_percent"] == pytest.approx(expected2["eta_indirect_percent"])
    assert draft2["eta_direct_percent"] != pytest.approx(draft["eta_direct_percent"])


def test_published_monthly_not_rewritten_but_revised(client, pool):
    """已发布的月度值不改写，而是生成修订说明。"""
    _setup_three_shifts(client)

    r = client.post(f"/monthly/{MONTH}/publish")
    assert r.status_code == 201, r.text
    published = r.json()["payload"]
    published_eta = published["eta_direct_percent"]

    # 发布后某班煤质复检（成分与发热量都改） → 月度草稿变化，但已发布值不动
    coal_v2 = make_coal(carbon_percent=54.0, ash_percent=22.7, qnet_kj_kg=20500.0)
    client.post(f"/shifts/{SHIFTS[1]}/coal-quality", json=coal_v2)

    monthly = client.get(f"/monthly/{MONTH}").json()
    assert monthly["published"]["payload"]["eta_direct_percent"] == pytest.approx(published_eta)
    assert monthly["draft"]["payload"]["eta_direct_percent"] != pytest.approx(published_eta)
    assert monthly["draft"]["payload"]["eta_indirect_percent"] != pytest.approx(
        published["eta_indirect_percent"]
    )

    # 修订说明：前后差异 + 引起变化的班次
    revisions = client.get(f"/monthly/{MONTH}/revisions").json()["revisions"]
    assert len(revisions) == 1
    detail = revisions[0]["detail"]
    assert detail["published"]["eta_direct_percent"] == pytest.approx(published_eta)
    assert detail["current"]["eta_direct_percent"] == pytest.approx(
        monthly["draft"]["payload"]["eta_direct_percent"]
    )
    assert detail["delta"]["eta_direct_percent"] == pytest.approx(
        detail["current"]["eta_direct_percent"] - detail["published"]["eta_direct_percent"]
    )
    changed_ids = [c["shift_id"] for c in detail["changed_shifts"]]
    assert changed_ids == [SHIFTS[1]]

    # 已发布版本仍可按原样取回（再次 GET 结果一致）
    again = client.get(f"/monthly/{MONTH}").json()
    assert again["published"]["payload"] == monthly["published"]["payload"]


def test_publish_without_draft_conflict(client):
    r = client.post("/monthly/2026-11/publish")
    assert r.status_code == 409


def test_monthly_not_found(client):
    r = client.get("/monthly/2026-11")
    assert r.status_code == 404
