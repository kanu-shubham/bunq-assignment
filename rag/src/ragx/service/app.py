"""HTTP surface.

Endpoints:
  POST /ask          - synchronous answer with citations
  POST /ask/stream   - SSE: contexts first, then tokens, then the verified result
  POST /ingest       - ingest a directory (in a real deployment: enqueue a sync job)
  DELETE /documents  - hard-delete documents from both indices
  GET  /healthz      - liveness + index size
  GET  /metrics      - Prometheus exposition

The principal comes from headers here for demo purposes. In production it comes
from a verified JWT/session and the tenant is *never* taken from a client-
supplied field — a header-trusted tenant id is a cross-tenant data leak with
extra steps. The shape of `Principal` is what matters: retrieval cannot be
called without one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Config
from ..factory import RagSystem, build
from ..obs import metrics
from ..obs.trace import configure_logging, new_trace_id
from ..types import Answer, Principal, Visibility

CORPUS_DIR = Path(__file__).resolve().parents[3] / "corpus"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    include_contexts: bool = False


class CitationOut(BaseModel):
    marker: int
    title: str
    path: str
    uri: str
    chunk_id: str
    quote: str | None = None


class AskResponse(BaseModel):
    answer: str
    abstained: bool
    grounding_score: float
    citations: list[CitationOut]
    trace_id: str
    timings_ms: dict[str, float]
    contexts: list[dict[str, Any]] | None = None
    debug: dict[str, Any] | None = None


class IngestRequest(BaseModel):
    directory: str | None = None
    tenant_id: str = "acme"


class DeleteRequest(BaseModel):
    doc_ids: list[str]


def create_app(system: RagSystem | None = None, config: Config | None = None) -> FastAPI:
    configure_logging()
    config = config or Config.from_env()
    state = system or build(config, offline=True)

    app = FastAPI(title="ragx", version="1.0.0", summary="RAG over company documentation")
    app.state.system = state

    def get_system() -> RagSystem:
        return app.state.system

    def get_principal(
        x_tenant_id: str = Header(default="acme"),
        x_subject_id: str = Header(default="anonymous"),
        x_groups: str = Header(default=""),
        x_max_visibility: str = Header(default="internal"),
    ) -> Principal:
        try:
            visibility = Visibility(x_max_visibility)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid x-max-visibility") from None
        groups = frozenset(g.strip() for g in x_groups.split(",") if g.strip())
        return Principal(
            tenant_id=x_tenant_id,
            subject_id=x_subject_id,
            groups=groups,
            max_visibility=visibility,
        )

    @app.post("/ask", response_model=AskResponse)
    def ask(
        body: AskRequest,
        request: Request,
        principal: Principal = Depends(get_principal),
        system: RagSystem = Depends(get_system),
    ) -> AskResponse:
        trace_id = request.headers.get("x-trace-id") or new_trace_id()
        result = system.pipeline.ask(body.question, principal, trace_id=trace_id)
        return _to_response(result.answer, result.debug, body.include_contexts)

    @app.post("/ask/stream")
    def ask_stream(
        body: AskRequest,
        request: Request,
        principal: Principal = Depends(get_principal),
        system: RagSystem = Depends(get_system),
    ) -> StreamingResponse:
        trace_id = request.headers.get("x-trace-id") or new_trace_id()

        def events() -> Iterator[str]:
            for kind, payload in system.pipeline.stream(
                body.question, principal, trace_id=trace_id
            ):
                if kind == "token":
                    yield _sse("token", {"text": payload})
                elif kind == "context":
                    yield _sse("context", payload)
                elif kind == "done":
                    assert isinstance(payload, Answer)
                    yield _sse(
                        "done",
                        _to_response(payload, None, body.include_contexts).model_dump(),
                    )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-trace-id": trace_id},
        )

    @app.post("/ingest")
    def ingest(
        body: IngestRequest, system: RagSystem = Depends(get_system)
    ) -> dict[str, Any]:
        root = Path(body.directory) if body.directory else CORPUS_DIR
        if not root.is_dir():
            raise HTTPException(status_code=400, detail=f"not a directory: {root}")
        from ..ingest.loaders import load_directory

        stats = system.ingest.ingest(load_directory(root, tenant_id=body.tenant_id))
        return {"indexed_chunks": len(system.vector_store), **stats.as_dict()}

    @app.delete("/documents")
    def delete_documents(
        body: DeleteRequest, system: RagSystem = Depends(get_system)
    ) -> dict[str, Any]:
        stats = system.ingest.delete_documents(body.doc_ids)
        return {"indexed_chunks": len(system.vector_store), **stats.as_dict()}

    @app.get("/healthz")
    def healthz(system: RagSystem = Depends(get_system)) -> dict[str, Any]:
        return {
            "status": "ok",
            "chunks": len(system.vector_store),
            "lexical_terms": len(system.bm25),
            "embedding_model": system.embedder.model_id,
            "answer_model": system.config.generation.model,
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus() -> str:
        return metrics.render_prometheus()

    return app


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _to_response(
    answer: Answer, debug: dict[str, Any] | None, include_contexts: bool
) -> AskResponse:
    return AskResponse(
        answer=answer.text,
        abstained=answer.abstained,
        grounding_score=round(answer.grounding_score, 3),
        citations=[
            CitationOut(
                marker=c.marker,
                title=c.title,
                path=c.display_path,
                uri=c.uri,
                chunk_id=c.chunk_id,
                quote=c.quote,
            )
            for c in sorted(answer.citations, key=lambda c: c.marker)
        ],
        trace_id=answer.trace_id,
        timings_ms=answer.stage_timings_ms,
        contexts=(
            [
                {
                    "marker": i,
                    "chunk_id": c.chunk.chunk_id,
                    "path": c.chunk.display_path,
                    "uri": c.chunk.uri,
                    "score": round(c.score, 4),
                    "channels": c.component_ranks,
                    "text": c.chunk.text,
                }
                for i, c in enumerate(answer.contexts, start=1)
            ]
            if include_contexts
            else None
        ),
        debug=debug,
    )


app = create_app()
