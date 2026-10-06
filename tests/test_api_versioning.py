"""版本化与煤质迟到/复检的集成测试（需要 PostgreSQL）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .fixtures import make_coal, make_config, make_run

SHIFT = "2026-10-06-A"


def test_coal_late_then_arrives_and_recheck_recalculates(client):
    """煤质迟到：只报待化验；到达后出效率；复检改数后产生新版本。"""
    # 1. 只有运行数据 → 待化验，不给效率
    r = client.post(f"/shifts/{SHIFT}/run-data", json=make_run())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_assay"
    assert body["calculation_version"] is None

    r = client.get(f"/shifts/{SHIFT}/result")
    assert r.status_code == 200
    assert r.json()["status"] == "pending_assay"
    assert "direct" not in r.json()

    # 2. 煤质到达 → 产生计算版本 1
    r = client.post(f"/shifts/{SHIFT}/coal-quality", json=make_coal())
    assert r.status_code == 201, r.text
    assert r.json()["calculation_version"] == 1

    result = client.get(f"/shifts/{SHIFT}/result").json()
    assert result["status"] == "ok"
    assert result["run_data_version"] == 1
    assert result["coal_quality_version"] == 1
    assert result["config_version"] == 1
    eta_v1 = result["indirect"]["eta_percent"]

    # 3. 化验室复检改数（灰分上调） → 新计算版本，效率变化
    coal_v2 = make_coal(ash_percent=22.2, carbon_percent=54.5)
    r = client.post(f"/shifts/{SHIFT}/coal-quality", json=coal_v2)
    assert r.json()["calculation_version"] == 2

    result = client.get(f"/shifts/{SHIFT}/result").json()
    assert result["calculation_version"] == 2
    assert result["coal_quality_version"] == 2
    assert result["indirect"]["eta_percent"] != pytest.approx(eta_v1)

    # 4. 旧版本仍可按版本号取回，且内容不变
    v1 = client.get(f"/shifts/{SHIFT}/calculations/1").json()
    assert v1["coal_quality_version"] == 1
    assert v1["indirect"]["eta_percent"] == pytest.approx(eta_v1)
    coal_v1 = client.get(f"/shifts/{SHIFT}/coal-quality/1").json()
    assert coal_v1["payload"]["ash_percent"] == pytest.approx(21.7)


def test_run_data_correction_creates_new_version(client):
    """运行数据补录/改正产生新计算版本，旧版本保留。"""
    client.post(f"/shifts/{SHIFT}/run-data", json=make_run())
    client.post(f"/shifts/{SHIFT}/coal-quality", json=make_coal())
    before = client.get(f"/shifts/{SHIFT}/result").json()

    corrected = make_run(coal_flow_tph=150.0)
    r = client.post(f"/shifts/{SHIFT}/run-data", json=corrected)
    assert r.json()["run_data_version"] == 2
    assert r.json()["calculation_version"] == 2

    after = client.get(f"/shifts/{SHIFT}/result").json()
    assert after["run_data_version"] == 2
    assert after["direct"]["eta_percent"] != pytest.approx(before["direct"]["eta_percent"])

    old = client.get(f"/shifts/{SHIFT}/calculations/1").json()
    assert old["run_data_version"] == 1
    original_run = client.get(f"/shifts/{SHIFT}/run-data/1").json()
    assert original_run["payload"]["coal_flow_tph"] == pytest.approx(142.0)


def test_config_change_recalculates_all_ready_shifts(client):
    """配置（容差/不确定度）调整 → 新配置版本 + 新计算版本。"""
    client.post(f"/shifts/{SHIFT}/run-data", json=make_run())
    client.post(f"/shifts/{SHIFT}/coal-quality", json=make_coal())
    before = client.get(f"/shifts/{SHIFT}/result").json()
    assert before["config_version"] == 1

    new_config = make_config(tolerance_percent=0.3)
    r = client.post("/config", json=new_config)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["config_version"] == 2
    assert {s["shift_id"] for s in body["recalculated_shifts"]} == {SHIFT}

    after = client.get(f"/shifts/{SHIFT}/result").json()
    assert after["calculation_version"] == 2
    assert after["config_version"] == 2
    assert after["tolerance_percent"] == pytest.approx(0.3)


def test_validation_error_names_field(client):
    """拒收并指出字段。"""
    bad = make_run(o2_percent=25.0)
    r = client.post(f"/shifts/{SHIFT}/run-data", json=bad)
    assert r.status_code == 422
    fields = [e["field"] for e in r.json()["detail"]]
    assert any("o2_percent" in f for f in fields)

    bad_coal = make_coal(qnet_kj_kg=-1.0)
    r = client.post(f"/shifts/{SHIFT}/coal-quality", json=bad_coal)
    assert r.status_code == 422
    assert any("qnet_kj_kg" in e["field"] for e in r.json()["detail"])


def test_reconciliation_reported_when_out_of_tolerance(client):
    """偏差超容差时结果中带对账信息，且怀疑对象指向被加偏差的燃煤量。"""
    client.post(f"/shifts/{SHIFT}/run-data", json=make_run())
    client.post(f"/shifts/{SHIFT}/coal-quality", json=make_coal())
    baseline = client.get(f"/shifts/{SHIFT}/result").json()
    assert baseline["within_tolerance"] is True
    assert baseline["reconciliation"] is None

    biased = make_run(coal_flow_tph=142.0 * 1.05)
    client.post(f"/shifts/{SHIFT}/run-data", json=biased)
    result = client.get(f"/shifts/{SHIFT}/result").json()
    assert result["within_tolerance"] is False
    rec = result["reconciliation"]
    assert rec is not None
    assert rec["primary_suspect"] == "coal_flow_tph"
    assert len(rec["adjustments"]) > 0
