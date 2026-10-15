# Temporal：正式支持的可选引擎（I-9）

> 裁决（2026-10-16 I-9）：Temporal 定为**正式支持的可选引擎**（不是实验特性，
> 也不是默认必选项）。按上市级标准，本文件 + CI 集成测试为必做件。

## 一句话

有 Temporal server 就用 durable workflow 跑任务；没有就**降级为本地执行**，
绝不因缺 server 而拒绝启动。`workflows/fake.py` 保证离线可用。

## 开关与状态

| 项 | 值 |
|---|---|
| 环境变量 | `FY_TEMPORAL_ADDRESS`（空 = 关闭）、`FY_TEMPORAL_NAMESPACE`（默认 `default`）、`FY_TEMPORAL_QUEUE`（默认 `find-yourself`） |
| 默认 | 关闭（`TemporalRuntime.disabled()`） |
| 降级可见 | `/health/ready` 报 `"temporal": "ready" \| "disabled"` + `degraded_markers`；关闭时绝不宣称 ready |
| 生产 | `config.py` 强制要求地址（无地址拒绝以生产模式启动） |
| 离线 | `workflows/fake.py` 顶替执行，不调网络 |

## 契约（冻结）

- 工作流名：`fy-task-workflow`；工作流 ID：`fy-task:<task_id>`（见 `runtime/temporal.py`）。
- API 进程只做 starter/signal/query（`TemporalRuntime`），**绝不在 API 进程跑 activity**；
  activity 与工作流本体在 worker 分片（`workflows/worker.py`）。
- 取消走协作式 `cancel` signal；DB 仍是事实源。

## 测试

- 单元/CI（无 server 可跑）：`tests/unit/test_temporal_optional.py`
  （默认关闭、不可达地址保持 disabled、工作流 ID 约定、健康二值）。
- 需真实 server + Postgres 的端到端：`tests/integration/test_temporal_live.py`
  与 `test_api_temporal_http.py`（门控跳过：`FY_DATABASE_URL` 非 postgresql、
  `127.0.0.1:7233` 不可达或无 `FY_RUN_HTTP_TEMPORAL=1` 即跳过，不在普通 CI 跑）。
