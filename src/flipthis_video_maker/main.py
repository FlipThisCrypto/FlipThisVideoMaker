import logging
import uuid

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from flipthis_video_maker import __version__
from flipthis_video_maker.api.resources import router as resources_router
from flipthis_video_maker.api.router import router
from flipthis_video_maker.config.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    settings.ensure_directories()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    app = FastAPI(title="FlipThisVideoMaker API", version=__version__, docs_url="/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(resources_router)

    @app.exception_handler(HTTPException)
    async def http_errors(request: Request, error: HTTPException) -> JSONResponse:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": "request_error",
                "detail": error.detail,
                "request_id": request_id,
            },
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_errors(request: Request, error: RequestValidationError) -> JSONResponse:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": jsonable_encoder(error.errors()),
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def errors(request: Request, error: Exception) -> JSONResponse:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.get_logger().exception(
            "request_failed", request_id=request_id, path=request.url.path, error=str(error)
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "An unexpected server error occurred",
                "request_id": request_id,
            },
        )

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "flipthis_video_maker.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )
