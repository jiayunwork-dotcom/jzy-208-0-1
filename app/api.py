"""接口层：班次数据写入、结果查询、配置管理、月度汇总与发布。"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from . import repository as repo
from .config import CalcConfig
from .schemas import CoalQuality, RunData
from .validation import (
    DomainValidationError,
    validate_coal_quality,
    validate_run_data,
)

router = APIRouter()


def _conn(request: Request) -> Iterator:
    with request.app.state.pool.connection() as conn:
        yield conn


class ShiftDataPayload(BaseModel):
    """运行数据与煤质同时到达时一次性提交，只产生一个新结果版本。"""

    run_data: RunData
    coal_quality: CoalQuality


def _result_view(row: dict) -> dict:
    return {
        "shift_id": row["shift_id"],
        "version": row["version"],
        "run_data_version": row["run_data_version"],
        "coal_quality_version": row["coal_quality_version"],
        "config_version": row["config_version"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "result": row["result"],
    }


# ---------------------------------------------------------------- 班次数据写入


@router.post("/shifts/{shift_id}/run-data", status_code=201)
def post_run_data(shift_id: str, run: RunData, conn=Depends(_conn)) -> dict:
    """录入/改正一个班次的运行数据，产生新的运行数据版本并重算。"""
    validate_run_data(run)
    with conn.transaction():
        old_date = repo.lock_shift(conn, shift_id, run.shift_date)
        run_data_version = repo.insert_run_data(conn, shift_id, run)
        result_version = repo.recompute_shift(conn, shift_id)
        _refresh_old_month_if_moved(conn, old_date, run.shift_date)
    return {
        "shift_id": shift_id,
        "run_data_version": run_data_version,
        "result_version": result_version,
    }


@router.post("/shifts/{shift_id}/coal-quality", status_code=201)
def post_coal_quality(shift_id: str, coal: CoalQuality, conn=Depends(_conn)) -> dict:
    """录入/复检一个班次的煤质化验，产生新的煤质版本并重算。"""
    validate_coal_quality(coal)
    with conn.transaction():
        repo.lock_shift(conn, shift_id)
        coal_quality_version = repo.insert_coal_quality(conn, shift_id, coal)
        result_version = repo.recompute_shift(conn, shift_id)
    return {
        "shift_id": shift_id,
        "coal_quality_version": coal_quality_version,
        "result_version": result_version,
    }


@router.post("/shifts/{shift_id}/data", status_code=201)
def post_shift_data(shift_id: str, payload: ShiftDataPayload, conn=Depends(_conn)) -> dict:
    """运行数据与煤质同时到达：同一事务写入，只产生一个新结果版本。"""
    errors: list[dict[str, str]] = []
    for validate, data in (
        (validate_run_data, payload.run_data),
        (validate_coal_quality, payload.coal_quality),
    ):
        try:
            validate(data)
        except DomainValidationError as exc:
            errors.extend(exc.errors)
    if errors:
        raise DomainValidationError(errors)

    with conn.transaction():
        old_date = repo.lock_shift(conn, shift_id, payload.run_data.shift_date)
        run_data_version = repo.insert_run_data(conn, shift_id, payload.run_data)
        coal_quality_version = repo.insert_coal_quality(conn, shift_id, payload.coal_quality)
        result_version = repo.recompute_shift(conn, shift_id)
        _refresh_old_month_if_moved(conn, old_date, payload.run_data.shift_date)
    return {
        "shift_id": shift_id,
        "run_data_version": run_data_version,
        "coal_quality_version": coal_quality_version,
        "result_version": result_version,
    }


def _refresh_old_month_if_moved(conn, old_date, new_date) -> None:
    """班次日期跨月改正时，原月份的月度汇总也要刷新。"""
    if old_date is None:
        return
    if (old_date.year, old_date.month) != (new_date.year, new_date.month):
        repo.refresh_monthly(conn, f"{old_date.year:04d}-{old_date.month:02d}")


# ---------------------------------------------------------------- 结果查询


@router.get("/shifts/{shift_id}/result")
def get_result(shift_id: str, version: int | None = None, conn=Depends(_conn)) -> dict:
    """取班次结果：默认最新版本，带 version 参数取历史版本。"""
    return _result_view(repo.get_result(conn, shift_id, version))


@router.get("/shifts/{shift_id}/results")
def list_results(shift_id: str, conn=Depends(_conn)) -> list[dict]:
    """班次全部结果版本及其依据的输入版本。"""
    return repo.list_results(conn, shift_id)


# ---------------------------------------------------------------- 配置


@router.post("/config", status_code=201)
def post_config(config: CalcConfig, conn=Depends(_conn)) -> dict:
    """写入新配置版本，并用新配置重算全部班次。"""
    with conn.transaction():
        version = repo.insert_config(conn, config)
        recomputed = repo.recompute_all_shifts(conn)
    return {"config_version": version, "recomputed_shifts": recomputed}


@router.get("/config")
def get_config(conn=Depends(_conn)) -> dict:
    version, payload = repo.current_config(conn)
    return {"config_version": version, "config": payload}


# ---------------------------------------------------------------- 月度


@router.get("/monthly/{month}")
def get_monthly(month: str, conn=Depends(_conn)) -> dict:
    return repo.get_monthly(conn, month)


@router.post("/monthly/{month}/publish", status_code=200)
def publish_monthly(month: str, conn=Depends(_conn)) -> dict:
    """发布月度值：冻结发布值并对当前结果快照，之后的变化只产生修订说明。"""
    with conn.transaction():
        repo.publish_monthly(conn, month)
        row = repo.get_monthly(conn, month)
    return row


@router.get("/monthly/{month}/revisions")
def list_revisions(month: str, conn=Depends(_conn)) -> list[dict]:
    return repo.list_revisions(conn, month)


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
