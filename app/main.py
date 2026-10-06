"""应用装配：连接池、建表、默认配置、异常处理。"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import db
from .api import router
from .repository import ConflictError, NotFoundError
from .validation import DomainValidationError

DEFAULT_DATABASE_URL = "postgresql://boiler:boiler@localhost:5432/boiler"


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.wait_and_init(url)
        app.state.pool = db.create_pool(url)
        yield
        app.state.pool.close()

    app = FastAPI(title="锅炉效率计算服务", lifespan=lifespan)

    @app.exception_handler(DomainValidationError)
    async def _domain_validation_handler(request, exc: DomainValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors})

    @app.exception_handler(NotFoundError)
    async def _not_found_handler(request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict_handler(request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.include_router(router)
    return app


app = create_app()
