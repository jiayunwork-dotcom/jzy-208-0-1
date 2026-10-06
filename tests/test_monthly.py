"""集成测试：月度汇总、发布后修订说明。"""
import math

from conftest import SAMPLE_COAL, SAMPLE_RUN


def _make_shift(client, shift_id, shift_date, **run_overrides):
    run = dict(SAMPLE_RUN, shift_date=shift_date, **run_overrides)
    assert client.post(f"/shifts/{shift_id}/run-data", json=run).status_code == 201
    assert client.post(f"/shifts/{shift_id}/coal-quality", json=SAMPLE_COAL).status_code == 201


def _expected_monthly(client, shift_ids):
    """从各班次最新结果从头汇总（与存储层同口径：按输出热量加权、math.fsum）。"""
    weights, directs, indirects = [], [], []
    for sid in sorted(shift_ids):
        result = client.get(f"/shifts/{sid}/result").json()["result"]
        weights.append(result["direct"]["output_heat"])
        directs.append(result["direct"]["efficiency"])
        indirects.append(result["indirect"]["efficiency"])
    total = math.fsum(weights)
    return (
        math.fsum(w * d for w, d in zip(weights, directs)) / total,
        math.fsum(w * i for w, i in zip(weights, indirects)) / total,
    )


def test_monthly_aggregation_matches_from_scratch(client):
    _make_shift(client, "S1", "2026-10-05")
    _make_shift(client, "S2", "2026-10-06", flue_gas_temp=150.0)
    _make_shift(client, "S3", "2026-11-01")  # 另一月份，不应混入

    monthly = client.get("/monthly/2026-10").json()
    assert monthly["shift_count"] == 2
    expected_direct, expected_indirect = _expected_monthly(client, ["S1", "S2"])
    assert monthly["direct_efficiency"] == expected_direct
    assert monthly["indirect_efficiency"] == expected_indirect


def test_monthly_updates_when_shift_data_changes(client):
    _make_shift(client, "S1", "2026-10-05")
    _make_shift(client, "S2", "2026-10-06", flue_gas_temp=150.0)
    before = client.get("/monthly/2026-10").json()

    # 改正 S1 的运行数据（排烟温度大幅升高）
    corrected = dict(SAMPLE_RUN, shift_date="2026-10-05", flue_gas_temp=180.0)
    assert client.post("/shifts/S1/run-data", json=corrected).status_code == 201

    after = client.get("/monthly/2026-10").json()
    assert after["indirect_efficiency"] != before["indirect_efficiency"]
    # 必须与用当前所有最新版本从头汇总的结果完全一致
    expected_direct, expected_indirect = _expected_monthly(client, ["S1", "S2"])
    assert after["direct_efficiency"] == expected_direct
    assert after["indirect_efficiency"] == expected_indirect


def test_published_monthly_gets_revision_note(client):
    _make_shift(client, "S1", "2026-10-05")
    _make_shift(client, "S2", "2026-10-06", flue_gas_temp=150.0)

    published = client.post("/monthly/2026-10/publish").json()
    assert published["published"] is True
    published_direct = published["published_direct"]
    published_indirect = published["published_indirect"]

    # 发布后 S2 煤质复检改数
    rechecked = dict(SAMPLE_COAL, qnet=SAMPLE_COAL["qnet"] * 0.9)
    assert client.post("/shifts/S2/coal-quality", json=rechecked).status_code == 201

    monthly = client.get("/monthly/2026-10").json()
    # 发布值不改写
    assert monthly["published_direct"] == published_direct
    assert monthly["published_indirect"] == published_indirect
    # 当前值已更新
    expected_direct, expected_indirect = _expected_monthly(client, ["S1", "S2"])
    assert monthly["direct_efficiency"] == expected_direct
    assert monthly["indirect_efficiency"] == expected_indirect

    revisions = client.get("/monthly/2026-10/revisions").json()
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision["old_direct"] == published_direct
    assert revision["old_indirect"] == published_indirect
    assert revision["new_direct"] == expected_direct
    assert revision["new_indirect"] == expected_indirect
    changed_ids = {c["shift_id"] for c in revision["changed_shifts"]}
    assert changed_ids == {"S2"}


def test_publish_twice_conflicts(client):
    _make_shift(client, "S1", "2026-10-05")
    assert client.post("/monthly/2026-10/publish").status_code == 200
    assert client.post("/monthly/2026-10/publish").status_code == 409


def test_publish_empty_month_404(client):
    assert client.post("/monthly/2030-01/publish").status_code == 404
    assert client.get("/monthly/2030-01").status_code == 404
