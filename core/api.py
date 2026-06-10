"""create_app() factory - turns a JurisdictionBase into a working FastAPI app.

Environment variables (all optional with defaults):
  LLM_BASE_URL      LLM endpoint (default: http://localhost:8080/v1)
  LLM_MODEL         model name (default: qwen3)
  QDRANT_URL        Qdrant endpoint (default: http://localhost:6333)
  REDIS_URL         Redis for web-verify cache (default: redis://127.0.0.1:6379/0)
  PUBLIC_TOKEN      token required in X-API-Key header (default: no auth)
  DEBUG_KEY         unlocks /ask/stream debug mode
  ALLOWED_ORIGIN    CORS origin (default: *)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.anchor import _augment_case_retrieval, _GUIDANCE_THRESHOLD, _refine_retrieve, _retrieve_anchor, _retrieve_manual_guidance
from core.browser import BrowserSession
from core.feedback import write_feedback, write_feedback_debug, write_feedback_full, write_route_debug
from core.jurisdiction import JurisdictionBase
from core.legislation import LegislationCache
from core.pipeline import RAGPipeline
from core.queue import (
    _AVG_QUERY_SECONDS,
    acquire, get_client_ip, global_llm_acquire, global_llm_release,
    global_llm_will_wait, LLM_GLOBAL_CONCURRENCY,
    queue_status, queue_wait_estimate, release, will_wait,
)
from core.retriever import VectorStore
from core.routing import build_route_decision
from core.sanitize import sanitize_question
from core.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from core.session import _format_session_context, _load_session, _save_session
from core.web_verify import _verify_sections, _web_verify

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
_LLM_MODEL = os.getenv("LLM_MODEL", "qwen3")
_REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
_PUBLIC_TOKEN = os.getenv("PUBLIC_TOKEN", "")
_DEBUG_KEY = os.getenv("DEBUG_KEY", "")
_ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
_VALID_STRATEGIES = {"vector", "mmr"}

_MODES: dict[str, str] = {
    "search":    "Do not generate a full legal answer. Instead, list the most relevant case references from the retrieved sources with a 1-2 sentence summary of what each decided. Format as a numbered list.\n\n",
    "case":      "Focus on Tribunal decisions and case outcomes. Cite specific case references and summarise what each Tribunal decided on this point.\n\n",
    "checklist": "Answer as a numbered step-by-step action checklist. Each step is a concrete action the user can take.\n\n",
    "landlord":  "Answer from the landlord's perspective. What rights, remedies, and obligations does the landlord have here?\n\n",
    "pitfalls":  "Focus your answer on common mistakes, traps, and risks to avoid. Lead with the pitfalls.\n\n",
}

_QUESTION_LOG = Path("data/question_log.jsonl")


def _log_question(question: str) -> None:
    try:
        _QUESTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _QUESTION_LOG.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "q": question}) + "\n")
    except Exception:
        pass

_REWRITE_SYSTEM_DEFAULT = (
    "Rewrite the following as a concise formal legal question optimised for retrieving relevant case decisions. "
    "Focus on the underlying legal dispute, facts, and claims (e.g. what damage is alleged, what the landlord or tenant is claiming, what the legal issue is). "
    "If the question includes procedural sub-questions about the tribunal process (wait times, hearing format, evidence deadlines), ignore those entirely - they are not useful for case retrieval. "
    "Output only the rewritten question, no explanation, no preamble."
)


def _check_token(request: Request) -> None:
    if not _PUBLIC_TOKEN:
        return
    if request.headers.get("X-API-Key") != _PUBLIC_TOKEN:
        raise HTTPException(status_code=401, detail={"error": "Invalid or missing API token."})


async def _check_llm() -> None:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{_LLM_BASE_URL}/models")
            if r.status_code != 200:
                raise Exception()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"error": "The AI model is currently loading. Please try again in 30 seconds."},
        )


def _strip_context_prefixes(question: str) -> str:
    """Remove leading [Key: value] context lines added by preprocess_question.

    Zone prefixes like '[Zone context: ...]' must not reach the rewriter - they
    bias vector retrieval toward planning/RMA sections instead of building law.
    The full prefixed question is still sent to the LLM for generation.
    """
    return re.sub(r"^(\[[^\]]+\]\s*\n+)+", "", question)


async def _rewrite_query(question: str, system_prompt: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_LLM_BASE_URL}/chat/completions",
                json={
                    "model": _LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    "max_tokens": 100,
                    "temperature": 0.0,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            r.raise_for_status()
            rewritten = r.json()["choices"][0]["message"]["content"].strip()
            return rewritten if rewritten else question
    except Exception:
        return question


def _confidence(scores: list[float], cfg=None) -> dict:
    from core.jurisdiction import ConfidenceConfig
    if cfg is None:
        cfg = ConfidenceConfig()
    n = len(scores)
    if n == 0:
        return {"level": "low", "chunks": 0, "message": cfg.messages.get("none", "No relevant sources found.")}
    top = max(scores)
    level = "high" if top >= cfg.high_score and n >= cfg.high_n else "medium" if top >= cfg.medium_score and n >= cfg.medium_n else "low"
    msg = cfg.messages.get(level, "").format(n=n)
    return {"level": level, "chunks": n, "message": msg}


class AskRequest(BaseModel):
    question: str
    session_id: str = ""
    debug_key: str = ""
    strategy: str = "vector"
    irac: bool = False
    verify: bool = True
    alwaysonline: bool = False
    address: str | None = None  # optional: geocoded to inject zone context via preprocess_question
    feedback_context: bool = False  # always emit context_debug for feedback capture (no debug_key required)
    user_context: str = ""          # client-local context stored in localStorage, injected into anchor
    mode: str = ""                  # cheat code mode (eli5, pitfalls, checklist, landlord, guardrail, eval-self, case, search)


class RetrieveRequest(BaseModel):
    question: str
    strategy: str = "vector"
    address: str | None = None


class FeedbackRequest(BaseModel):
    question: str
    rating: int
    comment: str = ""


class FeedbackFullRequest(BaseModel):
    question: str
    rating: int = 0
    comment: str = ""
    is_debug: bool = False
    strategy: str = ""
    irac: bool = False
    think: bool = False
    debug_mode: bool = False
    ts_start: str = ""
    ts_end: str = ""
    user_agent: str = ""
    answer: str = ""
    sources: list = []
    legislation: list = []
    confidence: dict | None = None
    web_results: dict | None = None
    verification: list | None = None
    debug: dict | None = None
    debug_timing: dict | None = None
    context_debug: dict | None = None


def create_app(
    jurisdiction: JurisdictionBase,
    pipeline_factory: type | None = None,
    static_dir: "Path | str | None" = None,
) -> FastAPI:
    """Return a fully configured FastAPI app for this jurisdiction.

    pipeline_factory: optional RAGPipeline subclass to instantiate instead of the default.
                      Must accept (collection, system_prompt, courts) keyword args.
    static_dir:       explicit path to a static files directory. Falls back to
                      jurisdictions/<name>/static/ inside the astraea package tree.
    """
    _default_static = (
        Path(__file__).parent.parent / "jurisdictions" / jurisdiction.name.replace("-", "_") / "static"
    )
    _static_dir = Path(static_dir) if static_dir is not None else _default_static

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        corpus = jurisdiction.corpus
        factory = pipeline_factory or RAGPipeline
        pipeline = factory(
            collection=corpus.qdrant_collection,
            system_prompt=jurisdiction.system_prompt,
            courts=corpus.courts or None,
        )
        leg_store = VectorStore(collection=corpus.leg_collection) if corpus.leg_collection else None
        leg_cache = LegislationCache()

        needs_browser = bool(jurisdiction.legislation or jurisdiction.web_verify)
        browser: BrowserSession | None = None
        if needs_browser:
            browser = BrowserSession()
            await browser.open()

        redis: aioredis.Redis | None = None
        if jurisdiction.web_verify:
            try:
                redis = aioredis.from_url(_REDIS_URL, decode_responses=True)
                await redis.ping()
            except Exception:
                redis = None

        if browser and jurisdiction.legislation:
            asyncio.create_task(leg_cache.warm(jurisdiction, browser))

        app.state.pipeline = pipeline
        app.state.leg_store = leg_store
        app.state.browser = browser
        app.state.redis = redis
        app.state.leg_cache = leg_cache
        app.state.jurisdiction = jurisdiction

        yield

        await pipeline.close()
        if browser:
            await browser.close()
        if redis:
            await redis.aclose()

    rewrite_system = (
        jurisdiction.rewrite_prompt
        if jurisdiction.rewrite_prompt is not None
        else _REWRITE_SYSTEM_DEFAULT
    )
    skip_rewrite = jurisdiction.rewrite_prompt == ""

    app = FastAPI(
        title=jurisdiction.description,
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_ALLOWED_ORIGIN],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    # Shared Astraea frontend utilities served at /static/astraea/astraea.js
    # Using explicit routes (not a StaticFiles mount) to avoid prefix conflict with /static.
    _astraea_frontend = Path(__file__).parent / "frontend"
    for _js_file in (_astraea_frontend.iterdir() if _astraea_frontend.is_dir() else []):
        _js_path = _astraea_frontend / _js_file.name

        @app.get(f"/static/astraea/{_js_file.name}", include_in_schema=False)
        async def _serve_astraea_static(
            _p: Path = _js_path,
        ) -> FileResponse:
            return FileResponse(_p, media_type="application/javascript")

    # Mount static files if the jurisdiction provides a static directory
    if _static_dir.exists():
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

        @app.get("/", include_in_schema=False)
        async def ui() -> FileResponse:
            return FileResponse(_static_dir / "index.html")

        robots_file = _static_dir / "robots.txt"
        if robots_file.exists():
            @app.get("/robots.txt", include_in_schema=False)
            async def robots() -> FileResponse:
                return FileResponse(robots_file, media_type="text/plain")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "jurisdiction": jurisdiction.name, **queue_status()}

    @app.get("/token")
    async def token() -> dict:
        return {"token": _PUBLIC_TOKEN}

    @app.get("/debug/ping", include_in_schema=False)
    async def debug_ping(request: Request) -> dict:
        _check_token(request)
        key = request.headers.get("X-Debug-Key", "")
        if not _DEBUG_KEY or key != _DEBUG_KEY:
            raise HTTPException(status_code=403, detail="Invalid debug key.")
        return {"ok": True}

    @app.post("/ask/stream")
    async def ask_stream(req: AskRequest, request: Request) -> StreamingResponse:
        _check_token(request)
        await _check_llm()
        question = sanitize_question(req.question.strip(), jurisdiction.max_question_chars)

        pipeline: RAGPipeline = request.app.state.pipeline
        leg_store: VectorStore | None = request.app.state.leg_store
        browser: BrowserSession | None = request.app.state.browser
        redis = request.app.state.redis
        leg_cache: LegislationCache = request.app.state.leg_cache
        jur: JurisdictionBase = request.app.state.jurisdiction
        question = jur.preprocess_question(question, address=req.address)

        debug_mode = bool(_DEBUG_KEY and req.debug_key == _DEBUG_KEY)
        strategy = req.strategy if debug_mode and req.strategy in _VALID_STRATEGIES else "vector"

        async def _event_stream():
            ip: str | None = None
            t0 = time.monotonic()
            try:
                if will_wait():
                    yield f"data: {json.dumps({'type': 'queue', **queue_wait_estimate()})}\n\n"
                try:
                    ip = await acquire(request)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
                    yield f"data: {json.dumps({'type': 'error', 'message': detail.get('error', 'Server busy.')})}\n\n"
                    return

                prior_turns = await _load_session(redis, jur.name, req.session_id)

                if not request.headers.get("X-No-Log"):
                    _log_question(question)

                retrieve_kwargs: dict = {"top_k": 5, "strategy": strategy, "min_score": 0.75, "min_chunks": 2}

                rewrite_input = _strip_context_prefixes(question)
                retrieval_question = (
                    rewrite_input if skip_rewrite
                    else await _rewrite_query(rewrite_input, rewrite_system)
                )

                (context_texts, sources), (anchor_vstore, leg_sources, ce_gate_log), (guidance_text, guidance_source, guidance_reason) = await asyncio.gather(
                    pipeline.retrieve(retrieval_question, **retrieve_kwargs),
                    _retrieve_anchor(retrieval_question, question, pipeline, leg_store, jur),
                    _retrieve_manual_guidance(retrieval_question, question, pipeline, set(), jur),
                )

                # Inject MANUAL guidance chunk if it scored above threshold and is not
                # already among the retrieved corpus hits.
                guidance_injected = False
                if guidance_text and guidance_source:
                    existing_ids = {s["case_id"] for s in sources}
                    if guidance_source["case_id"] not in existing_ids:
                        context_texts = [guidance_text] + context_texts
                        sources = [guidance_source] + sources
                        guidance_injected = True

                context_texts, sources = await _augment_case_retrieval(
                    question, retrieval_question, pipeline, jur, context_texts, sources,
                )

                refine_used = False
                if _confidence([s["_score"] for s in sources], jur.confidence_config)["level"] == "low":
                    context_texts, sources = await _refine_retrieve(
                        question, retrieval_question, pipeline, sources, context_texts,
                    )
                    refine_used = True

                t_retrieve = time.monotonic() - t0

                web_text, web_results, from_cache = "", [], False
                if req.verify and browser:
                    web_text, web_results, from_cache = await _web_verify(
                        retrieval_question, leg_sources, browser, redis, jur,
                        alwaysonline=req.alwaysonline,
                    )

                if not context_texts and not anchor_vstore:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'I could not find enough relevant decisions to answer this question reliably.'})}\n\n"
                    return

                scores = [s["_score"] for s in sources]
                public_sources = [{k: v for k, v in s.items() if k not in ("title", "_score")} for s in sources]

                # Build live anchor if cached legislation text is available (zero extra latency)
                live_anchor = ""
                if jur.legislation:
                    first_act_id = next(iter(jur.legislation.acts), None)
                    if first_act_id:
                        live_text = leg_cache.get(first_act_id, jur.legislation.cache_ttl_seconds)
                        if live_text and leg_sources:
                            live_anchor = leg_cache.build_anchor(first_act_id, live_text, leg_sources, jur)

                anchor = live_anchor or anchor_vstore
                if web_text:
                    anchor = (anchor + "\n\n---\n\n" if anchor else "") + web_text

                session_ctx = _format_session_context(prior_turns)
                if session_ctx:
                    anchor = session_ctx + ("\n\n---\n\n" + anchor if anchor else "")

                user_ctx = req.user_context.strip()[:500] if req.user_context else ""
                if user_ctx:
                    anchor = "User's personal context (apply throughout your answer):\n" + user_ctx + ("\n\n---\n\n" + anchor if anchor else "")

                yield f"data: {json.dumps({'type': 'sources', 'sources': public_sources, 'legislation': leg_sources})}\n\n"
                if web_results:
                    yield f"data: {json.dumps({'type': 'web_results', 'results': web_results, 'cached': from_cache})}\n\n"
                yield f"data: {json.dumps({'type': 'confidence', **_confidence(scores, jur.confidence_config)})}\n\n"

                if debug_mode:
                    yield f"data: {json.dumps({'type': 'debug', 'strategy': strategy, 'retrieve_ms': round(t_retrieve * 1000), 'scores': scores, 'chunks': len(scores), 'refine_used': refine_used})}\n\n"

                _no_log = request.headers.get("X-No-Log")
                _wants_route_log = jur.log_route_decisions and not _no_log
                if debug_mode or req.feedback_context or _wants_route_log:
                    def _tok(text: str) -> int:
                        return max(1, round(len(text) / 4))

                    decision = build_route_decision(question, retrieval_question, jur.routes)
                    routing_ev = {
                        "triggered": decision.triggered,
                        "matched_routes": list(decision.matched_intents),
                        "trigger_terms": list(decision.trigger_terms),
                        "trigger_paths": {intent: path for intent, path in decision.trigger_paths},
                        "forced_sections": list(decision.forced_sections),
                        "dominant_route": decision.dominant_route,
                        "dominance_reason": decision.dominance_reason,
                        "ignored_routes": [
                            {"route": r, "reason": reason}
                            for r, reason in decision.ignored_routes
                        ],
                        "near_miss_routes": [
                            {"route": intent, "broad_matched": list(terms)}
                            for intent, terms in decision.near_miss_routes
                        ],
                        "ce_gate": ce_gate_log,
                    }
                    anchor_sections = [
                        {
                            "document_id": s.get("case_id", ""),
                            "title": s.get("title", ""),
                            "tokens": 0,
                            "preview": "",
                            "forbidden_terms": {},
                        }
                        for s in leg_sources
                    ]
                    chunk_cards = [
                        {
                            "source_index": i + 1,
                            "score": s.get("_score", 0),
                            "passed_gate": True,
                            "document_id": s.get("case_id", ""),
                            "date": s.get("date", ""),
                            "tokens": _tok(txt),
                            "preview": txt[:300],
                            "full_text": txt,
                        }
                        for i, (s, txt) in enumerate(zip(sources, context_texts))
                    ]
                    anchor_tok = _tok(anchor)
                    chunk_tok = sum(_tok(txt) for txt in context_texts)
                    budget = {
                        "total_tokens": anchor_tok + chunk_tok,
                        "ctx_limit": 8192,
                        "anchor_tokens": anchor_tok,
                        "chunk_tokens": chunk_tok,
                        "sources_sent": len(sources),
                        "truncated_chunks": 0,
                    }
                    guidance_ev = {
                        "injected": guidance_injected,
                        "source": guidance_source["case_id"] if guidance_source else None,
                        "court_name": guidance_source["court_name"] if guidance_source else None,
                        "score": guidance_source["_score"] if guidance_source else None,
                        "threshold": _GUIDANCE_THRESHOLD,
                        "reason": guidance_reason,
                    }
                    yield f"data: {json.dumps({'type': 'context_debug', 'original_query': question, 'rewrite_input': rewrite_input, 'rewritten_query': retrieval_question, 'rewrite_used': retrieval_question != rewrite_input, 'statute_routing': routing_ev, 'anchor': {'method': 'vector+cache', 'sections': anchor_sections}, 'guidance': guidance_ev, 'chunks': chunk_cards, 'budget': budget})}\n\n"

                # Apply cheat code mode: prefix only the generation question.
                # Retrieval already used the clean question above.
                gen_question = question
                if req.mode:
                    mode_prefix = _MODES.get(req.mode.lower().lstrip("/"), "")
                    if mode_prefix:
                        gen_question = mode_prefix + question

                # Global LLM semaphore: serialize generation across all app
                # processes when LLM_GLOBAL_CONCURRENCY > 0. Retrieval above
                # already ran in parallel; only inference is serialized.
                if LLM_GLOBAL_CONCURRENCY and await global_llm_will_wait(redis):
                    yield f"data: {json.dumps({'type': 'queue', 'position': 1, 'reason': 'llm_busy', 'estimated_wait_s': _AVG_QUERY_SECONDS, 'message': 'Another query is generating - queued.'})}\n\n"

                global_acquired = await global_llm_acquire(redis, timeout=90.0)
                if not global_acquired:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'The server is too busy right now. Please try again in a moment.'})}\n\n"
                    return

                t_gen = time.monotonic()
                full_answer: list[str] = []
                try:
                    async for tok in pipeline._generator.generate_stream(
                        gen_question, context_texts, sources, legislation_anchor=anchor or None
                    ):
                        full_answer.append(tok)
                        yield f"data: {json.dumps({'type': 'token', 'text': tok})}\n\n"
                finally:
                    await global_llm_release(redis)

                if debug_mode:
                    yield f"data: {json.dumps({'type': 'debug_done', 'generate_ms': round((time.monotonic() - t_gen) * 1000), 'total_ms': round((time.monotonic() - t0) * 1000)})}\n\n"

                yield f"data: {json.dumps({'type': 'done'})}\n\n"

                if _wants_route_log:
                    try:
                        write_route_debug(
                            question,
                            retrieval_question,
                            routing_ev,
                            answer="".join(full_answer),
                            sources=sources,
                            legislation=leg_sources,
                            strategy=strategy,
                        )
                    except Exception:
                        pass

                await _save_session(redis, jur.name, req.session_id, question, "".join(full_answer))

                verification = await _verify_sections("".join(full_answer), leg_sources, leg_cache, jur)
                if verification:
                    yield f"data: {json.dumps({'type': 'verification', 'sections': verification})}\n\n"

            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            finally:
                if ip:
                    release(ip)

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/retrieve")
    async def retrieve(req: RetrieveRequest, request: Request) -> dict:
        _check_token(request)
        question = sanitize_question(req.question.strip(), jurisdiction.max_question_chars)

        pipeline: RAGPipeline = request.app.state.pipeline
        leg_store: VectorStore | None = request.app.state.leg_store
        leg_cache: LegislationCache = request.app.state.leg_cache
        jur: JurisdictionBase = request.app.state.jurisdiction
        question = jur.preprocess_question(question, address=req.address)

        strategy = req.strategy if req.strategy in _VALID_STRATEGIES else "vector"
        rewrite_input = _strip_context_prefixes(question)
        retrieval_question = (
            rewrite_input if skip_rewrite
            else await _rewrite_query(rewrite_input, rewrite_system)
        )

        (context_texts, sources), (anchor_vstore, leg_sources, _ce_gate_log), (guidance_text, guidance_source, guidance_reason) = await asyncio.gather(
            pipeline.retrieve(retrieval_question, top_k=5, strategy=strategy, min_score=0.75, min_chunks=2),
            _retrieve_anchor(retrieval_question, question, pipeline, leg_store, jur),
            _retrieve_manual_guidance(retrieval_question, question, pipeline, set(), jur),
        )

        context_texts, sources = await _augment_case_retrieval(
            question, retrieval_question, pipeline, jur, context_texts, sources,
        )

        if _confidence([s["_score"] for s in sources], jur.confidence_config)["level"] == "low":
            context_texts, sources = await _refine_retrieve(
                question, retrieval_question, pipeline, sources, context_texts,
            )

        live_anchor = ""
        if jur.legislation:
            first_act_id = next(iter(jur.legislation.acts), None)
            if first_act_id:
                live_text = leg_cache.get(first_act_id, jur.legislation.cache_ttl_seconds)
                if live_text and leg_sources:
                    live_anchor = leg_cache.build_anchor(first_act_id, live_text, leg_sources, jur)

        anchor = live_anchor or anchor_vstore
        public_sources = [
            {**{k: v for k, v in s.items() if k != "title"}, "_score": s.get("_score")}
            for s in sources
        ]

        guidance_result = None
        if guidance_source:
            guidance_result = {
                "injected": guidance_source["case_id"] not in {s.get("case_id") for s in public_sources},
                "source": guidance_source["case_id"],
                "court_name": guidance_source.get("court_name"),
                "score": guidance_source.get("_score"),
                "reason": guidance_reason,
            }

        return {
            "context_texts": context_texts,
            "sources": public_sources,
            "legislation": leg_sources,
            "anchor": anchor,
            "guidance": guidance_result,
        }

    @app.post("/feedback")
    async def feedback(req: FeedbackRequest, request: Request) -> dict:
        _check_token(request)
        write_feedback(request, req.question, req.rating, req.comment)
        return {"ok": True}

    @app.post("/feedback/full")
    async def feedback_full(req: FeedbackFullRequest, request: Request) -> dict:
        _check_token(request)
        if not req.is_debug and req.rating not in (1, -1):
            raise HTTPException(status_code=400, detail="Rating must be 1 or -1.")
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "jurisdiction": jurisdiction.name,
            "rating": req.rating,
            "comment": req.comment[:1000],
            "strategy": req.strategy,
            "irac": req.irac,
            "think": req.think,
            "debug_mode": req.debug_mode,
            "ts_start": req.ts_start,
            "ts_end": req.ts_end,
            "user_agent": req.user_agent[:300],
            "question": req.question[:2000],
            "answer": req.answer[:8000],
            "sources": req.sources,
            "legislation": req.legislation,
            "confidence": req.confidence,
            "web_results": req.web_results,
            "verification": req.verification,
            "debug": req.debug,
            "debug_timing": req.debug_timing,
            "context_debug": req.context_debug,
        }
        if req.is_debug:
            write_feedback_debug(request, entry)
        else:
            write_feedback_full(request, entry)
        return {"ok": True}

    jurisdiction.register_routes(app)

    return app
