"""集成测试：版本化（煤质迟到、复检、运行数据改正、配置变更）与接口校验。"""
from conftest import SAMPLE_COAL, SAMPLE_RUN


def _post_run(client, shift_id, run):
    resp = client.post(f"/shifts/{shift_id}/run-data", json=run)
    assert resp.status_code == 201, resp.json()
    return resp.json()


def _post_coal(client, shift_id, coal):
    resp = client.post(f"/shifts/{shift_id}/coal-quality", json=coal)
    assert resp.status_code == 201, resp.json()
    return resp.json()


def test_pending_until_coal_quality_arrives(client, run_payload, coal_payload):
    """煤质未到时只报"待化验"，不给效率；煤质到达后给出两种效率。"""
    _post_run(client, "S1", run_payload)

    pending = client.get("/shifts/S1/result").json()
    assert pending["status"] == "pending_assay"
    assert pending["result"] == {"status": "pending_assay"}
    assert pending["coal_quality_version"] is None

    _post_coal(client, "S1", coal_payload)
    computed = client.get("/shifts/S1/result").json()
    assert computed["status"] == "computed"
    assert computed["version"] == 2
    assert computed["run_data_version"] == 1
    assert computed["coal_quality_version"] == 1
    assert computed["config_version"] == 1
    result = computed["result"]
    assert 80.0 < result["direct"]["efficiency"] < 100.0
    assert 80.0 < result["indirect"]["efficiency"] < 100.0
    for key in (
        "q2_flue_gas",
        "q3_chemical_incomplete",
        "q4_mechanical_incomplete",
        "q5_radiation",
        "q6_ash_physical",
    ):
        assert key in result["indirect"]


def test_coal_recheck_produces_new_version_and_keeps_old(client, run_payload, coal_payload):
    """化验复检改数 -> 新计算版本；旧版本保留且可按版本号取回。"""
    _post_run(client, "S1", run_payload)
    _post_coal(client, "S1", coal_payload)
    first = client.get("/shifts/S1/result").json()

    rechecked = dict(coal_payload, qnet=coal_payload["qnet"] * 0.9)
    _post_coal(client, "S1", rechecked)
    second = client.get("/shifts/S1/result").json()

    assert second["version"] == 3
    assert second["coal_quality_version"] == 2
    assert (
        second["result"]["direct"]["efficiency"]
        != first["result"]["direct"]["efficiency"]
    )

    # 旧版本按版本号取回，内容不变
    old = client.get("/shifts/S1/result", params={"version": first["version"]}).json()
    assert old["result"] == first["result"]
    assert old["coal_quality_version"] == 1


def test_run_data_correction_produces_new_version(client, run_payload, coal_payload):
    _post_run(client, "S1", run_payload)
    _post_coal(client, "S1", coal_payload)
    before = client.get("/shifts/S1/result").json()

    corrected = dict(run_payload, flue_gas_temp=run_payload["flue_gas_temp"] + 20.0)
    resp = _post_run(client, "S1", corrected)
    assert resp["run_data_version"] == 2

    after = client.get("/shifts/S1/result").json()
    assert after["version"] == before["version"] + 1
    assert after["run_data_version"] == 2
    assert after["coal_quality_version"] == 1
    assert (
        after["result"]["indirect"]["q2_flue_gas"]
        > before["result"]["indirect"]["q2_flue_gas"]
    )


def test_results_list_tracks_input_versions(client, run_payload, coal_payload):
    _post_run(client, "S1", run_payload)
    _post_coal(client, "S1", coal_payload)
    _post_coal(client, "S1", dict(coal_payload, moisture=coal_payload["moisture"] + 0.1))

    versions = client.get("/shifts/S1/results").json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[0]["status"] == "pending_assay"
    assert versions[1]["coal_quality_version"] == 1
    assert versions[2]["coal_quality_version"] == 2


def test_config_change_recomputes_all_shifts(client, run_payload, coal_payload):
    _post_run(client, "S1", run_payload)
    _post_coal(client, "S1", coal_payload)
    _post_run(client, "S2", dict(run_payload, shift_date="2026-10-06"))
    _post_coal(client, "S2", coal_payload)

    config = client.get("/config").json()["config"]
    config["tolerance_pp"] = 0.3
    resp = client.post("/config", json=config)
    assert resp.status_code == 201, resp.json()
    assert resp.json()["config_version"] == 2
    assert resp.json()["recomputed_shifts"] == 2

    for shift_id in ("S1", "S2"):
        latest = client.get(f"/shifts/{shift_id}/result").json()
        assert latest["config_version"] == 2


def test_api_rejects_invalid_run_data_with_field_names(client, run_payload):
    bad = dict(run_payload, flue_o2=25.0)
    resp = client.post("/shifts/S1/run-data", json=bad)
    assert resp.status_code == 422
    fields = {e["field"] for e in resp.json()["detail"]}
    assert "flue_o2" in fields

    bad = dict(run_payload, fly_ash_share=0.7, slag_share=0.1)
    resp = client.post("/shifts/S1/run-data", json=bad)
    assert resp.status_code == 422
    assert "fly_ash_share" in {e["field"] for e in resp.json()["detail"]}


def test_api_rejects_invalid_coal_quality_with_field_names(client, coal_payload):
    bad = dict(coal_payload, carbon=coal_payload["carbon"] + 2.0)
    resp = client.post("/shifts/S1/coal-quality", json=bad)
    assert resp.status_code == 422
    assert "elemental_sum" in {e["field"] for e in resp.json()["detail"]}

    bad = dict(coal_payload, qnet=-1.0)
    resp = client.post("/shifts/S1/coal-quality", json=bad)
    assert resp.status_code == 422
    assert "qnet" in {e["field"] for e in resp.json()["detail"]}


def test_api_rejects_non_positive_uncertainty(client):
    config = client.get("/config").json()["config"]
    config["uncertainties"]["coal_flow"] = {"relative_pct": -1.0}
    resp = client.post("/config", json=config)
    assert resp.status_code == 422


def test_unknown_shift_result_returns_404(client):
    assert client.get("/shifts/NOPE/result").status_code == 404
