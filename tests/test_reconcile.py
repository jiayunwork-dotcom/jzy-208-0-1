"""对账（数据协调）单元测试。"""
import pytest

from app.config import MEASUREMENT_KEYS, CalcConfig
from app.engine import compute_shift_result
from app.schemas import CoalQuality, RunData

from conftest import SAMPLE_COAL, SAMPLE_RUN, make_consistent


def _result(run_dict, coal_dict=None, config=None):
    run = RunData(**run_dict)
    coal = CoalQuality(**(coal_dict or SAMPLE_COAL))
    return compute_shift_result(run, coal, config or CalcConfig())


def test_consistent_data_gives_zero_adjustment():
    """两法已经一致的数据送进去，调整量必须为零。"""
    run = make_consistent(SAMPLE_RUN, SAMPLE_COAL)
    rec = _result(run)["reconciliation"]

    assert rec["status"] == "consistent"
    assert abs(rec["difference_pp"]) <= CalcConfig().tolerance_pp
    assert all(v == 0.0 for v in rec["adjustments"].values())
    assert rec["prime_suspect"] is None


def test_biased_coal_flow_points_to_coal_flow():
    """人为给燃煤量加 5% 偏差，首要怀疑对象应指向燃煤量。"""
    run = make_consistent(SAMPLE_RUN, SAMPLE_COAL)
    biased = dict(run)
    biased["coal_flow"] = run["coal_flow"] * 1.05

    rec = _result(biased)["reconciliation"]

    assert rec["status"] == "adjusted"
    assert rec["prime_suspect"] == "coal_flow"
    # 调整方向正确（往回扣），且燃煤量的 σ 倍数在所有测量中最大
    assert rec["adjustments"]["coal_flow"] < 0.0
    sigmas = rec["adjustments_in_sigma"]
    assert sigmas["coal_flow"] == max(sigmas.values())
    assert sigmas["coal_flow"] > 1.0
    # 调整后两法一致
    assert rec["direct_efficiency_adjusted"] == pytest.approx(
        rec["indirect_efficiency_adjusted"], abs=1e-6
    )


def test_adjustment_concentrates_on_most_uncertain_measurement():
    """调整量与不确定度相称：燃煤量不确定度占绝对优势时，偏差几乎全由它吸收。"""
    run = make_consistent(SAMPLE_RUN, SAMPLE_COAL)
    biased = dict(run)
    biased["coal_flow"] = run["coal_flow"] * 1.05

    config = CalcConfig(uncertainties={"coal_flow": {"relative_pct": 10.0}})
    result = compute_shift_result(
        RunData(**biased), CoalQuality(**SAMPLE_COAL), config
    )
    rec = result["reconciliation"]

    assert rec["prime_suspect"] == "coal_flow"
    # 绝大部分偏差由燃煤量吸收（>85%）
    assert rec["adjustments"]["coal_flow"] == pytest.approx(
        -0.05 * biased["coal_flow"], rel=0.15
    )


def test_bias_beyond_uncertainty_budget_is_reported():
    """偏差大到不确定度预算内无法调和时，明确报告而不是硬凑。"""
    run = make_consistent(SAMPLE_RUN, SAMPLE_COAL)
    biased = dict(run)
    biased["coal_flow"] = run["coal_flow"] * 1.20  # 偏差 20%，σ=2%，远超 3σ

    rec = _result(biased)["reconciliation"]

    assert rec["status"] == "exceeds_uncertainty_budget"
    assert rec["prime_suspect"] == "coal_flow"
    assert rec["adjustments_in_sigma"]["coal_flow"] > CalcConfig().max_sigma_multiple


def test_all_measurements_participate():
    run = make_consistent(SAMPLE_RUN, SAMPLE_COAL)
    rec = _result(run)["reconciliation"]
    assert set(rec["adjustments"]) == set(MEASUREMENT_KEYS)
