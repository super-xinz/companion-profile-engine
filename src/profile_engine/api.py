from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .demo import router as demo_router
from .workspace import router as workspace_router
from .models import IdempotencyRecord, RulePack
from .extractor import SemanticExtractorError
from .rule_compiler import compile_rule_pack
from .schemas import CorrectionRequest, ForgetRequest, MessageIngestRequest, ProfileInitRequest
from .service import (ConsentError, NotFoundError, VersionConflictError, correct_profile,
                      ensure_rule_pack, explain_profile, find_user, forget_profile, get_profile,
                      ingest_message, init_profile, request_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        # A rule published from the expert workspace is the production source
        # of truth and must survive service restarts. Files only bootstrap an
        # empty database.
        pack = db.scalar(select(RulePack).where(
            RulePack.status == "published"
        ).order_by(desc(RulePack.published_at)).limit(1))
        if not pack:
            settings = get_settings()
            source = settings.rule_source_dir
            if not source.is_absolute():
                source = (Path.cwd() / source).resolve()
            pack = ensure_rule_pack(db, compile_rule_pack(source))
        app.state.rule_pack_id = pack.id
    yield


app = FastAPI(
    title="陪伴机器人真人画像引擎",
    version="0.2.0",
    description="可审计、可更正、可遗忘的真人用户画像状态机。",
    lifespan=lifespan,
)
app.include_router(demo_router)
app.include_router(workspace_router)
app.mount("/assets", StaticFiles(directory=str(files("profile_engine").joinpath("static"))), name="assets")


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or request_id()
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


@app.exception_handler(NotFoundError)
async def not_found(request: Request, exc: NotFoundError):
    return _error(request, 404, "not_found", str(exc))


@app.exception_handler(ConsentError)
async def consent_error(request: Request, exc: ConsentError):
    return _error(request, 403, "consent_required", str(exc))


@app.exception_handler(VersionConflictError)
async def version_conflict(request: Request, exc: VersionConflictError):
    return _error(request, 409, "profile_version_conflict", "画像版本不匹配",
                  {"expected_profile_version": exc.expected, "actual_profile_version": exc.actual})


@app.exception_handler(ValueError)
async def invalid_value(request: Request, exc: ValueError):
    return _error(request, 422, "invalid_operation", str(exc))


@app.exception_handler(SemanticExtractorError)
async def semantic_extractor_error(request: Request, exc: SemanticExtractorError):
    return _error(request, 503, "semantic_extractor_unavailable", str(exc))


def _error(request: Request, status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"request_id": getattr(request.state, "request_id", request_id()),
        "code": code, "message": message, "details": details or {}})


def auth_context(
    x_api_key: str = Header(alias="X-API-Key"),
    x_tenant_id: str = Header(alias="X-Tenant-ID", min_length=1, max_length=128),
) -> str:
    settings = get_settings()
    expected = settings.tenant_api_keys.get(x_tenant_id, settings.api_key if settings.environment == "development" else "")
    if not expected or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid API key")
    return x_tenant_id


def idempotency_key(value: str = Header(alias="Idempotency-Key", min_length=1, max_length=256)) -> str:
    return value


def current_pack(request: Request, db: Session) -> RulePack:
    pack = db.get(RulePack, request.app.state.rule_pack_id)
    if not pack:
        pack = db.scalar(select(RulePack).where(RulePack.status == "published").order_by(desc(RulePack.published_at)).limit(1))
    if not pack:
        raise RuntimeError("没有已发布规则包")
    return pack


def _request_hash(body: object) -> str:
    value = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cached(db: Session, tenant_id: str, key: str, body: object) -> dict | None:
    record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == tenant_id, IdempotencyRecord.idempotency_key == key))
    if not record:
        return None
    if record.request_hash != _request_hash(body):
        raise ValueError("同一 Idempotency-Key 不能用于不同请求体")
    return record.response_body


def _cache(db: Session, tenant_id: str, key: str, body: object, response: dict) -> None:
    db.add(IdempotencyRecord(tenant_id=tenant_id, idempotency_key=key, request_hash=_request_hash(body), status_code=200, response_body=response))
    db.commit()


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "service": "companion-profile-engine", "version": "0.2.0"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/demo", status_code=307)


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page() -> HTMLResponse:
    html = files("profile_engine").joinpath("static/demo.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/rules", response_class=HTMLResponse, include_in_schema=False)
def rules_page() -> HTMLResponse:
    html = files("profile_engine").joinpath("static/rules.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/v1/profiles:init", tags=["profiles"])
def initialize(body: ProfileInitRequest, request: Request, tenant_id: str = Depends(auth_context),
               idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, body)
    if cached: return cached
    pack = current_pack(request, db)
    response = init_profile(db, tenant_id, body, pack, request.state.request_id, idem)
    _cache(db, tenant_id, idem, body, response)
    return response


@app.get("/v1/profiles/{user_id}", tags=["profiles"])
def read_profile(user_id: str, tenant_id: str = Depends(auth_context), db: Session = Depends(get_db)) -> dict:
    return get_profile(db, tenant_id, user_id)


@app.post("/v1/profiles/{user_id}/messages:ingest", tags=["messages"])
def ingest(user_id: str, body: MessageIngestRequest, request: Request, tenant_id: str = Depends(auth_context),
           idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, body)
    if cached: return cached
    pack = current_pack(request, db)
    response = ingest_message(db, tenant_id, user_id, body, pack, request.state.request_id, idem)
    _cache(db, tenant_id, idem, body, response)
    return response


@app.get("/v1/profiles/{user_id}/explain", tags=["profiles"])
def explain(user_id: str, field: str | None = Query(default=None), tenant_id: str = Depends(auth_context),
            db: Session = Depends(get_db)) -> dict:
    return explain_profile(db, tenant_id, user_id, field)


@app.post("/v1/profiles/{user_id}:correct", tags=["profiles"])
def correct(user_id: str, body: CorrectionRequest, request: Request, tenant_id: str = Depends(auth_context),
            idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, body)
    if cached: return cached
    response = correct_profile(db, tenant_id, user_id, body, current_pack(request, db), request.state.request_id, idem)
    _cache(db, tenant_id, idem, body, response)
    return response


@app.post("/v1/profiles/{user_id}:forget", tags=["profiles"])
def forget(user_id: str, body: ForgetRequest, request: Request, tenant_id: str = Depends(auth_context),
           idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, body)
    if cached: return cached
    response = forget_profile(db, tenant_id, user_id, body, current_pack(request, db), request.state.request_id, idem)
    _cache(db, tenant_id, idem, body, response)
    return response


@app.get("/v1/rule-packs/current", tags=["rules"])
def rule_pack_current(request: Request, tenant_id: str = Depends(auth_context), db: Session = Depends(get_db)) -> dict:
    pack = current_pack(request, db)
    return {"version": pack.version, "sha256": pack.sha256, "status": pack.status,
            "validation_report": pack.validation_report, "published_at": pack.published_at}
