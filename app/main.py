"""FastAPI 接口层。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from . import repository as repo
from . import service
from .db import create_pool, init_schema
from .schemas import DEFAULT_CONFIG, AppConfig, CoalQuality, RunData


def create_app(pool: ConnectionPool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 未显式传入连接池时（如 uvicorn 直接加载模块级 app），
        # 到 startup 再连库，避免导入期阻塞。
        if not hasattr(app.state, "pool"):
            app.state.pool = create_pool()
            init_schema(app.state.pool, seed_config=DEFAULT_CONFIG)
            app.state.owns_pool = True
        yield
        if app.state.owns_pool and hasattr(app.state, "pool"):
            app.state.pool.close()

    app = FastAPI(title="锅炉效率试验服务", version="1.0.0", lifespan=lifespan)

    if pool is not None:
        init_schema(pool, seed_config=DEFAULT_CONFIG)
        app.state.pool = pool
        app.state.owns_pool = False

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        # 拒收并指出字段：把 pydantic 的 loc 拼成点分路径
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    def get_pool() -> ConnectionPool:
        return app.state.pool

    # ------------------------------------------------------------------
    # 班次数据
    # ------------------------------------------------------------------

    @app.post("/shifts/{shift_id}/run-data", status_code=201)
    def post_run_data(shift_id: str, body: RunData):
        try:
            return service.submit_run_data(get_pool(), shift_id, body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/shifts/{shift_id}/coal-quality", status_code=201)
    def post_coal_quality(shift_id: str, body: CoalQuality):
        try:
            return service.submit_coal_quality(get_pool(), shift_id, body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/shifts/{shift_id}/result")
    def get_shift_result(shift_id: str):
        with get_pool().connection() as conn:
            run = repo.latest_versioned(conn, "run_data", shift_id)
            coal = repo.latest_versioned(conn, "coal_quality", shift_id)
            calc = repo.latest_calculation(conn, shift_id)
            if calc is not None:
                version, result = calc
                return {"calculation_version": version, **result}
            if run is None and coal is None:
                raise HTTPException(status_code=404, detail=f"班次 {shift_id} 无任何数据")
            return {
                "shift_id": shift_id,
                "status": "pending_assay" if coal is None else "pending_run_data",
                "run_data_version": run[0] if run else None,
                "coal_quality_version": coal[0] if coal else None,
            }

    @app.get("/shifts/{shift_id}/calculations/{version}")
    def get_calculation(shift_id: str, version: int):
        with get_pool().connection() as conn:
            result = repo.get_calculation(conn, shift_id, version)
            if result is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"班次 {shift_id} 不存在计算版本 {version}",
                )
            return {"calculation_version": version, **result}

    @app.get("/shifts/{shift_id}/run-data/{version}")
    def get_run_data_version(shift_id: str, version: int):
        with get_pool().connection() as conn:
            payload = repo.get_versioned(conn, "run_data", shift_id, version)
            if payload is None:
                raise HTTPException(
                    status_code=404, detail=f"班次 {shift_id} 不存在运行数据版本 {version}"
                )
            return {"shift_id": shift_id, "run_data_version": version, "payload": payload}

    @app.get("/shifts/{shift_id}/coal-quality/{version}")
    def get_coal_quality_version(shift_id: str, version: int):
        with get_pool().connection() as conn:
            payload = repo.get_versioned(conn, "coal_quality", shift_id, version)
            if payload is None:
                raise HTTPException(
                    status_code=404, detail=f"班次 {shift_id} 不存在煤质版本 {version}"
                )
            return {"shift_id": shift_id, "coal_quality_version": version, "payload": payload}

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    @app.post("/config", status_code=201)
    def post_config(body: AppConfig):
        return service.update_config(get_pool(), body.model_dump())

    @app.get("/config/latest")
    def get_latest_config():
        with get_pool().connection() as conn:
            version, payload = repo.latest_config(conn)
            return {"config_version": version, "payload": payload}

    # ------------------------------------------------------------------
    # 月度
    # ------------------------------------------------------------------

    @app.get("/monthly/{month}")
    def get_monthly(month: str):
        try:
            return service.get_monthly(get_pool(), month)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/monthly/{month}/publish", status_code=201)
    def publish_monthly(month: str):
        try:
            return service.publish_month(get_pool(), month)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/monthly/{month}/revisions")
    def get_monthly_revisions(month: str):
        with get_pool().connection() as conn:
            return {"month": month, "revisions": repo.list_monthly_revisions(conn, month)}

    return app


app = create_app()
