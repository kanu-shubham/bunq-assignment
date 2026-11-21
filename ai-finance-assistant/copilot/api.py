"""Skill #4: serving it. A small FastAPI service.

Run:  uvicorn copilot.api:app --reload
Docs: http://localhost:8000/docs
"""
import logging
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request

from .categorizer import LLMCategorizer, RuleCategorizer
from .data import load_transactions
from .llm import has_credentials
from .agent import MoneyCopilot
from .models import ChatRequest, ChatResponse, CategorizedItem, Transaction
from .retriever import BM25Retriever
from .tools import FinanceTools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("copilot.api")

app = FastAPI(title="Money Copilot", version="1.0")
retriever = BM25Retriever.from_dir()
sessions: dict[str, MoneyCopilot] = {}  # in-memory; use Redis/DB in production
MAX_SESSIONS = 1000


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    log.info("%s %s -> %d in %.0fms", request.method, request.url.path,
             response.status_code, (time.perf_counter() - start) * 1000)
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_enabled": has_credentials()}


@app.get("/transactions")
def transactions() -> list[Transaction]:
    return list(load_transactions())


@app.post("/categorize")
def categorize(txs: list[Transaction]) -> list[CategorizedItem]:
    if not txs or len(txs) > 200:
        raise HTTPException(422, "Send between 1 and 200 transactions")
    categorizer = LLMCategorizer() if has_credentials() else RuleCategorizer()
    return categorizer.categorize(txs)


@app.get("/help/search")
def help_search(q: str, k: int = 3) -> list[dict]:
    return [{"source": c.citation, "text": c.text} for c in retriever.search(q, k=min(k, 10))]


@app.post("/chat")
def chat(req: ChatRequest, x_session_id: str | None = Header(default=None)) -> ChatResponse:
    if not has_credentials():
        raise HTTPException(503, "Set ANTHROPIC_API_KEY to enable chat")
    sid = x_session_id or str(uuid.uuid4())
    if sid not in sessions:
        if len(sessions) >= MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))
        sessions[sid] = MoneyCopilot(FinanceTools(categorizer=LLMCategorizer(), retriever=retriever))
    result = sessions[sid].chat(req.message)
    log.info("chat session=%s tools=%s tokens=%d/%d cost=$%.4f", sid, result.tool_calls,
             result.input_tokens, result.output_tokens, result.cost_usd)
    return result
