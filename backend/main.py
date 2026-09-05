"""FastAPI application entrypoint.

Run: uvicorn backend.main:app --reload
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import router
from backend.config import settings
from backend.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ai_teacher")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("AI Teacher ready — LLM=%s, TTS=%s, avatar=%s",
             settings.llm.provider, settings.tts.provider, settings.avatar.provider)
    yield


app = FastAPI(
    title="AI Teacher",
    description="An AI educator that plans lessons, teaches through generated video, "
                "questions the student, and adapts to their answers.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")


@app.get("/health", tags=["debug"])
def health():
    return {"status": "ok", "app": settings.app_name}


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": str(exc), "path": request.url.path})
