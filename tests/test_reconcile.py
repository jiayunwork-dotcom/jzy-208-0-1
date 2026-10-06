"""对账调整单元测试（无需数据库）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.direct import direct_efficiency
from app.indirect import heat_losses
from app.reconcile import efficiency_difference, reconcile

from .fixtures import make_coal, make_config, make_run


def _consistent_pair():
    """构造一对正/反平衡恰好一致的数据：先算反平衡效率，再反解煤量。"""
    run = make_run()
    coal = make_coal()
    config = make_config()
    eta_indirect = heat_losses(run, coal, config)["eta_percent"]
    # eta_direct = 100 * output / (B * qnet) = eta_indirect  =>  B = ...
    output = direct_efficiency(run, coal, config)["output_heat_kj_h"]
    run["coal_flow_tph"] = 100.0 * output / (eta_indirect * coal["qnet_kj_kg"])
    return run, coal, config


def test_zero_adjustment_when_methods_agree():
    """两法已经一致的数据送进去，所有调整量必须为零。"""
    run, coal, config = _consistent_pair()
    assert efficiency_difference(run, coal, config) == pytest.approx(0.0, abs=1e-9)
    result = reconcile(run, coal, config)
    for adj in result["adjustments"]:
        assert adj["delta"] == pytest.approx(0.0, abs=1e-12)
        assert adj["delta_in_sigmas"] == pytest.approx(0.0, abs=1e-12)
    assert result["primary_suspect"] is None
    assert result["reconcilable_within_uncertainty"] is True
    assert result["violations"] == []


def test_biased_coal_flow_points_to_coal_flow():
    """人为给燃煤量加偏差后，首要怀疑对象必须指向燃煤量。"""
    run, coal, config = _consistent_pair()
    run["coal_flow_tph"] *= 1.05  # 皮带秤偏高 5%
    result = reconcile(run, coal, config)
    assert result["primary_suspect"] == "coal_flow_tph"
    # 调整方向：把煤量调低
    coal_adj = next(a for a in result["adjustments"] if a["name"] == "coal_flow_tph")
    assert coal_adj["delta"] < 0.0
    # 调整后两法应基本一致（一阶近似的残差很小）
    assert abs(result["residual_after_adjustment"]) < 0.05
    # 5% 的偏差在 2% 相对不确定度下约 2.5σ，仍应可在 3σ 内分摊
    assert result["reconcilable_within_uncertainty"] is True


def test_unreconcilable_bias_is_reported():
    """偏差大到不确定度兜不住时：照常给出调整，但明确报告无法完成。"""
    run, coal, config = _consistent_pair()
    run["coal_flow_tph"] *= 1.30  # 30% 的偏差，远超 2% 不确定度
    result = reconcile(run, coal, config)
    assert result["reconcilable_within_uncertainty"] is False
    assert "coal_flow_tph" in result["violations"]
    assert result["note"] is not None
    # 调整量仍然给出，且方向上仍在纠正煤量
    coal_adj = next(a for a in result["adjustments"] if a["name"] == "coal_flow_tph")
    assert coal_adj["delta"] < 0.0


def test_adjustments_rescale_with_uncertainties():
    """把燃煤量不确定度调得很小后，同样的偏差应改由其他测量承担更多。"""
    run, coal, config = _consistent_pair()
    run["coal_flow_tph"] *= 1.05
    tight = make_config()
    tight["uncertainties"]["relative"]["coal_flow_tph"] = 0.0001
    result = reconcile(run, coal, tight)
    assert result["primary_suspect"] != "coal_flow_tph"


def test_reconcile_does_not_mutate_inputs():
    run, coal, config = _consistent_pair()
    run["coal_flow_tph"] *= 1.05
    snapshot = (dict(run), dict(coal))
    reconcile(run, coal, config)
    assert run == snapshot[0]
    assert coal == snapshot[1]
