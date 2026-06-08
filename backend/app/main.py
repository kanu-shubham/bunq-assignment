"""FastAPI application entry point."""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services.database import init_db
from app.api import workflows, audit, events, tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="HITL Financial Workflow System",
    description="Human-in-the-Loop stack for settlements, reconciliations, disputes, and regulatory submissions.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(tools.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
