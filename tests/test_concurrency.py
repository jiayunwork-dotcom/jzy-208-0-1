"""集成测试：并发到达不产生中间版本；同批到达只产生一个版本。"""
import threading

from conftest import SAMPLE_COAL, SAMPLE_RUN


def test_concurrent_coal_and_run_data_arrival(client):
    """化验结果与运行数据改正并发到达同一班次：

    最终最新结果版本必须同时基于两者，不能停留在只反映一方的中间状态。
    """
    run = dict(SAMPLE_RUN)
    coal = dict(SAMPLE_COAL)
    assert client.post("/shifts/S1/run-data", json=run).status_code == 201
    assert client.post("/shifts/S1/coal-quality", json=coal).status_code == 201

    corrected_run = dict(run, flue_gas_temp=run["flue_gas_temp"] + 15.0)
    rechecked_coal = dict(coal, qnet=coal["qnet"] * 0.95)

    barrier = threading.Barrier(2)
    outcomes = {}

    def post_run():
        barrier.wait()
        outcomes["run"] = client.post("/shifts/S1/run-data", json=corrected_run)

    def post_coal():
        barrier.wait()
        outcomes["coal"] = client.post("/shifts/S1/coal-quality", json=rechecked_coal)

    threads = [threading.Thread(target=post_run), threading.Thread(target=post_coal)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes["run"].status_code == 201, outcomes["run"].json()
    assert outcomes["coal"].status_code == 201, outcomes["coal"].json()

    latest = client.get("/shifts/S1/result").json()
    # 最新版本同时基于新的运行数据与新的煤质
    assert latest["run_data_version"] == 2
    assert latest["coal_quality_version"] == 2
    # 每个输入版本变化各产生一个结果版本，无丢失、无重复
    versions = client.get("/shifts/S1/results").json()
    assert [v["version"] for v in versions] == [1, 2, 3, 4]


def test_combined_submission_produces_single_version(client):
    """运行数据与煤质同一批提交：只产生一个新结果版本。"""
    payload = {"run_data": dict(SAMPLE_RUN), "coal_quality": dict(SAMPLE_COAL)}
    resp = client.post("/shifts/S1/data", json=payload)
    assert resp.status_code == 201, resp.json()
    assert resp.json()["result_version"] == 1

    latest = client.get("/shifts/S1/result").json()
    assert latest["status"] == "computed"
    assert latest["run_data_version"] == 1
    assert latest["coal_quality_version"] == 1

    # 再次同批提交（改正 + 复检同时到达）
    payload2 = {
        "run_data": dict(SAMPLE_RUN, flue_o2=6.0),
        "coal_quality": dict(SAMPLE_COAL, qnet=SAMPLE_COAL["qnet"] * 1.02),
    }
    resp = client.post("/shifts/S1/data", json=payload2)
    assert resp.status_code == 201
    assert resp.json()["result_version"] == 2

    latest = client.get("/shifts/S1/result").json()
    assert latest["run_data_version"] == 2
    assert latest["coal_quality_version"] == 2
    versions = client.get("/shifts/S1/results").json()
    assert [v["version"] for v in versions] == [1, 2]
