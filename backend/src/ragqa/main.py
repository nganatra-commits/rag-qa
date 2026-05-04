"""FastAPI app entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ragqa import __version__
from ragqa.api.routes import router
from ragqa.config import get_settings
from ragqa.core.errors import RagQaError
from ragqa.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger("ragqa.main")
    log.info("ragqa.startup", version=__version__,
             index=settings.pinecone_index, namespace=settings.pinecone_namespace)
    yield
    log.info("ragqa.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RagQA",
        description="Image-friendly RAG over the NWA Quality Analyst documentation set.",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RagQaError)
    async def _domain_err(_: Request, exc: RagQaError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    app.include_router(router)
    return app


app = create_app()
