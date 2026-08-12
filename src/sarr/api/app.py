"""FastAPI application factory used by Lambda (Mangum) and local Docker."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sarr.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SARR Search API",
        description="Semantic Artifacts Retrieval and Ranking for PyPI packages",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_origin_regex=r"https://.*\.github\.io$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
