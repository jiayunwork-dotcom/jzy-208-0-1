# 锅炉效率试验服务

燃煤电厂性能试验用后端：每班同时用**正平衡（输入-输出法）**和**反平衡（热损失法）**
计算锅炉效率，超差时按测量不确定度自动对账，全部数据与结果版本化，月度加权汇总，
已发布月度值只修订不改写。

- Python 3.12 / FastAPI / PostgreSQL 16，无网页界面
- `docker compose up` 一键启动（app + postgres:16-alpine）

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/combustion.py` | 理论空气量、过量空气系数、烟气量（由元素分析推出） |
| `app/direct.py` | 正平衡效率 |
| `app/indirect.py` | 反平衡各项热损失 q2~q6 |
| `app/reconcile.py` | 正反平衡对账（不确定度加权分摊） |
| `app/repository.py` | 版本库（SQL 存取，咨询锁） |
| `app/service.py` | 编排：计算触发、配置变更重算、月度汇总与修订 |
| `app/main.py` | FastAPI 接口层 |
| `app/schemas.py` | 数据模型与入库校验 |
| `app/db.py` | 连接池与建表 |

## 计算模型

### 燃烧（每 kg 收到基燃料）

- 理论空气量 `V0 = 0.0889·(C+0.375·S) + 0.265·H − 0.0333·O` Nm³/kg
  （纯碳 C=100% 时 V0 = 8.89 Nm³/kg，有单元测试锚定）
- 过量空气系数 `α = 21 / (21 − (O2 − 0.5·CO))`，CO 折算回未耗氧；
  O2=0 且 CO=0 时 α=1（有单元测试锚定）
- 烟气量：RO₂、N₂、H₂O 理论量 + (α−1)·V0 过量空气

### 反平衡

`η = 100 − (q2+q3+q4+q5+q6)`，其中：

- **q2 排烟损失**：(干烟气量×1.38 + 水蒸气量×1.51)·(排烟温度−环境温度)，随排烟温度单调上升（有测试）
- **q3 化学不完全燃烧**：干烟气量 × CO 浓度 × 12636 kJ/Nm³
- **q4 机械不完全燃烧**：灰分中未燃尽碳 × 32866 kJ/kg；飞灰含碳为零时只剩炉渣部分（有测试）
- **q5 散热损失（取法为设计决定）**：默认取配置固定值 `q5_fixed_percent`；
  配置 `q5_mode="load_scaled"` 时按额定负荷折算 `q5_rated·D_rated/D_actual`。
  选固定值为默认是因为本场景（每班核算）负荷波动小，且避免引入额定曲线拟合误差
- **q6 灰渣物理热**：飞灰按排烟温度、炉渣按配置排渣温度（默认 600℃），比热 0.96 kJ/(kg·K)
- q2、q3 按 GB/T 10184 习惯乘 (1−q4/100) 修正

### 正平衡

`η = [D主汽·(h主汽−h给水) + D再热·(h再热出−h再热进) − D排污·(h排污水−h给水)] / (B·Qnet) × 100%`

排污水焓无直接测量，取配置值（默认 1150 kJ/kg，近似汽包压力饱和水焓）。

## 对账（差额分摊）设计

两法差值超过 `tolerance_percent` 时触发。设 `f(x) = η正(x) − η反(x)`，
求调整量 δ 使 `f(x+δ)=0`，在一阶泰勒近似下解加权最小二乘：

```
min Σ(δᵢ/uᵢ)²   s.t. Σ cᵢδᵢ = −f(x)，cᵢ = ∂f/∂xᵢ（中心差分数值求取）
→   δᵢ = −f · cᵢ·uᵢ² / Σ(cⱼuⱼ)²
```

迭代（高斯-牛顿，≤5 轮）消除线性化残差。取舍说明：

- **每个测量都允许调整**，调整量正比于 灵敏度×不确定度²——不确定度大、
  对差额影响大的测量自然分到更多，这正是不确定度传播的含义；
- **首要怀疑对象** = |δᵢ/uᵢ| 最大的测量（相对自身不确定度偏得最多的那个）；
- **两法已一致时** δ 恒为零、无怀疑对象（有测试锚定）；
- **无法在不确定度范围内完成时**（某 |δᵢ| > `sigma_limit`·uᵢ，默认 3σ）：
  照常给出最小二乘调整（它是"最不至于冤枉仪表"的解释），但
  `reconcilable_within_uncertainty=false` 并列出超限测量与提示，
  因为差额已大到不像正常计量误差，需人工排查；
- 参与对账的 15 个测量及其不确定度全部来自配置，流量/焓/发热量用相对
  不确定度，温度/氧量/浓度/含碳量用绝对不确定度。

## 版本化模型

- `run_data` / `coal_quality` / `config` / `calculation` 四张版本表，
  (业务键, version) 主键，旧版本永不改写，可按版本号取回；
- 每次运行数据补录/改正、煤质复检、配置调整都产生**新的计算版本**，
  结果 JSON 中注明 `run_data_version` / `coal_quality_version` / `config_version`；
- 煤质未到的班次只报 `pending_assay`，不出效率；
- 配置变更会对所有数据齐备的班次批量产生新计算版本。

## 月度汇总

- 权重 = 各班燃料输入热量（入炉煤量 × 低位发热量）；
- **变化即整体重算**：任何班次产生新计算版本时，用该月所有班次的最新计算
  版本从头汇总为新草稿——因此月度值必然与"用当前所有最新版本从头汇总"
  完全一致（有测试锚定）；
- `POST /monthly/{month}/publish` 把当前草稿发布为不可变正式值；
  发布后再有变化，**已发布值不改写**，而是生成 `monthly_revision` 修订说明，
  列出前后效率、差值和引起变化的班次。

## 并发设计

同一班次的写操作在事务内先取 `pg_advisory_xact_lock`（班次命名空间），
再读最新版本、插入、计算。化验与运行数据改正并发到达时，两个事务被串行化，
后提交者读到双方都已落库的最新版本——**最新计算版本永远不会停留在只基于
其中一方的中间状态**（中间结果若产生，只会作为历史版本存在）。月度重算用
月份命名空间的咨询锁，锁顺序恒为"班次→月份"，无死锁。

## 入库校验（拒收并指出字段，HTTP 422）

- 元素质量分数之和与 100% 偏差 > 0.5 个百分点
- 发热量不为正
- 排烟氧量不在 [0, 21]
- 主蒸汽焓 ≤ 给水焓
- 灰渣比例之和 ≠ 1
- 任一不确定度不为正（或缺失）

## API 一览

```
POST /shifts/{shift_id}/run-data        提交运行数据（shift_id 形如 2026-10-06-A）
POST /shifts/{shift_id}/coal-quality    提交煤质化验
GET  /shifts/{shift_id}/result          最新结果（或 pending_assay）
GET  /shifts/{shift_id}/calculations/{version}   取回历史计算版本
GET  /shifts/{shift_id}/run-data/{version}       取回历史运行数据
GET  /shifts/{shift_id}/coal-quality/{version}   取回历史煤质
POST /config                            新配置版本（触发全量重算）
GET  /config/latest
GET  /monthly/{month}                   当前草稿 + 已发布值
POST /monthly/{month}/publish           发布月度值
GET  /monthly/{month}/revisions         修订说明列表
```

## 运行

```bash
docker compose up --build        # app: http://localhost:8000/docs
```

## 测试

```bash
pip install -r requirements.txt -r requirements-dev.txt

# 单元测试（燃烧/损失/正平衡/对账/校验，无需数据库）
python -m pytest tests/test_combustion.py tests/test_indirect.py \
    tests/test_direct.py tests/test_reconcile.py tests/test_validation.py

# 集成测试需要 PostgreSQL 16，二选一：
#   a) 已有数据库：BOILER_TEST_DSN=postgresql://user:pass@host/db python -m pytest
#   b) 自动下载临时 PostgreSQL 16（免 root 免 docker）：
./scripts/start_test_db.sh
BOILER_TEST_DSN=postgresql://postgres@localhost:55432/postgres python -m pytest
```

测试覆盖：纯碳理论空气量 8.89；氧量/CO 为零时 α=1；排烟温度与飞灰含碳趋势；
两法一致时调整量为零；燃煤量加偏差后首要怀疑对象指向燃煤量；煤质迟到与复检重算；
月度值与从头汇总一致；已发布月度值的修订说明；并发到达不产生中间版本。
