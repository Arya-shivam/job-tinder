import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from . import accounts, matching
from .config import Settings
from .db import migrate, pool_for
from .models import ModelResponseError, Models, ModelUnavailable


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with pool_for(settings.database_url) as db, Redis.from_url(settings.redis_url) as redis:
            await migrate(db)
            await redis.ping()
            app.state.db, app.state.redis = db, redis
            app.state.models = models = Models(settings)
            try:
                yield
            finally:
                await models.close()

    app = FastAPI(title="Job Match", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def private_api(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        origin = request.headers.get("origin")
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            cross_site or (origin is not None and origin != settings.web_origin)
        ):
            return JSONResponse({"detail": "This request must come from the app's own origin."}, status_code=403)
        operation = {"/api/chat": "chat_request", "/api/digest/run": "digest_request"}.get(request.url.path)
        if operation and request.method == "POST":
            with request.app.state.models.operation(operation):
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Check the format and length of the submitted fields."}, status_code=422)

    @app.exception_handler(ModelUnavailable)
    async def provider_unavailable(request: Request, exc: ModelUnavailable):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ModelResponseError)
    async def provider_failed(request: Request, exc: ModelResponseError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/healthz", include_in_schema=False)
    async def healthy():
        async with app.state.db.connection() as conn:
            await conn.execute("SELECT 1")
        await app.state.redis.ping()
        return {"status": "ok"}

    app.include_router(accounts.router)
    app.include_router(matching.router)
    return app


if __name__ == "__main__":
    config = uvicorn.Config(create_app(), host="0.0.0.0", port=8000, access_log=False)
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)
