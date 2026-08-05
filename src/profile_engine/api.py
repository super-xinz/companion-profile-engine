from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, Security
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, desc, select, text
from sqlalchemy.orm import Session

from . import __version__
from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .demo import router as demo_router
from .workspace import router as workspace_router
from .models import IdempotencyRecord, RulePack, User
from .extractor import SemanticExtractorError
from .model_gateway import public_model_options
from .rule_compiler import compile_rule_pack
from .security import SlidingWindowRateLimiter, constant_time_equal, safe_request_id
from .schemas import (Consent, CorrectionRequest, ForgetRequest, MessageIngestRequest,
                      ProfileInitRequest, ResetProfileRequest, SetEnneagramRequest)
from .service import (ConsentError, NotFoundError, VersionConflictError, correct_profile,
                      ensure_rule_pack, explain_profile, forget_profile, get_profile,
                      ingest_message, init_profile, request_id, set_enneagram_profile)
from .workspace import _sync_template_people


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_runtime_configuration()
    init_db()
    with SessionLocal() as db:
        # A rule published from the expert workspace is the production source
        # of truth and must survive service restarts. Files only bootstrap an
        # empty database.
        pack = db.scalar(select(RulePack).where(
            RulePack.status == "published"
        ).order_by(desc(RulePack.published_at)).limit(1))
        settings = get_settings()
        source = settings.rule_source_dir
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        compiled = compile_rule_pack(source)
        # Production keeps the expert-published database revision as source of
        # truth. Development/test must follow the checked-out rule files so a
        # branch update cannot silently run against an obsolete local DB pack.
        if (not pack or "enneagram" not in pack.canonical_json
                or (not settings.is_production and pack.sha256 != compiled.sha256)):
            pack = ensure_rule_pack(db, compiled)
        tenant_ids = [row[0] for row in db.execute(select(User.tenant_id).distinct()).all()]
        for tenant_id in tenant_ids:
            _sync_template_people(db, tenant_id, pack, ensure_conversation=False)
        db.commit()
        app.state.rule_pack_id = pack.id
    yield


_startup_settings = get_settings()
app = FastAPI(
    title="陪伴机器人真人画像引擎",
    version=__version__,
    description="可审计、可更正、可遗忘的真人用户画像状态机。",
    lifespan=lifespan,
    docs_url="/docs" if _startup_settings.api_docs_active else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _startup_settings.api_docs_active else None,
)
app.include_router(demo_router)
app.include_router(workspace_router)
app.mount("/assets", StaticFiles(directory=str(files("profile_engine").joinpath("static"))), name="assets")

logger = logging.getLogger("profile_engine.api")
logger.setLevel(logging.INFO)


def _apply_security_headers(response: Response, request: Request, req_id: str, settings) -> None:
    response.headers["X-Request-ID"] = req_id
    response.headers["X-API-Version"] = "1"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'; object-src 'none'"
    )
    if request.url.path.startswith(("/v1", "/demo/api")):
        response.headers["Cache-Control"] = "no-store"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    started = time.perf_counter()
    req_id = safe_request_id(request.headers.get("X-Request-ID"), request_id())
    request.state.request_id = req_id
    settings = get_settings()
    content_length = request.headers.get("Content-Length")
    if request.method in {"POST", "PUT", "PATCH"} and content_length is None:
        response = _error(request, 411, "length_required", "请求必须提供 Content-Length")
        _apply_security_headers(response, request, req_id, settings)
        return response
    if content_length:
        try:
            parsed_length = int(content_length)
            too_large = parsed_length < 0 or parsed_length > settings.max_request_body_bytes
        except ValueError:
            too_large = True
        if too_large:
            response = _error(
                request, 413, "request_too_large",
                f"请求体不能超过 {settings.max_request_body_bytes} 字节",
            )
            _apply_security_headers(response, request, req_id, settings)
            return response
    response = await call_next(request)
    _apply_security_headers(response, request, req_id, settings)
    logger.info(json.dumps({
        "timestamp": time.time(),
        "level": "info",
        "service": "companion-profile-engine",
        "request_id": req_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }, ensure_ascii=False, separators=(",", ":")))
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


_api_key_header = APIKeyHeader(name="X-API-Key", scheme_name="TenantApiKey", auto_error=False)
_rate_limiter = SlidingWindowRateLimiter()
_auth_failure_limiter = SlidingWindowRateLimiter()


def auth_context(
    request: Request,
    response: Response,
    x_api_key: str | None = Security(_api_key_header),
    x_tenant_id: str = Header(
        alias="X-Tenant-ID", min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
) -> str:
    settings = get_settings()
    expected = settings.tenant_api_keys.get(x_tenant_id, settings.api_key if settings.environment == "development" else "")
    authenticated = bool(x_api_key and expected and constant_time_equal(x_api_key, expected))
    if not authenticated:
        source = request.client.host if request.client else "unknown"
        allowed, _, retry_after = _auth_failure_limiter.check(
            source, settings.auth_failure_rate_limit_per_minute
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="too many authentication failures",
                headers={"Retry-After": str(retry_after)},
            )
        raise HTTPException(status_code=401, detail="invalid API key", headers={"WWW-Authenticate": "ApiKey"})
    allowed, remaining, retry_after = _rate_limiter.check(x_tenant_id, settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="tenant rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return x_tenant_id


def idempotency_key(value: str = Header(
    alias="Idempotency-Key", min_length=1, max_length=256,
    pattern=r"^[A-Za-z0-9._:-]+$",
)) -> str:
    return value


def current_pack(request: Request, db: Session) -> RulePack:
    pack = db.get(RulePack, request.app.state.rule_pack_id)
    if not pack:
        pack = db.scalar(select(RulePack).where(RulePack.status == "published").order_by(desc(RulePack.published_at)).limit(1))
    if not pack:
        raise RuntimeError("没有已发布规则包")
    return pack


def _request_hash(request: Request, body: object) -> str:
    value = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    envelope = {"method": request.method, "path": request.url.path, "body": value}
    return hashlib.sha256(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cached(db: Session, tenant_id: str, key: str, request: Request, body: object) -> dict | None:
    record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == tenant_id, IdempotencyRecord.idempotency_key == key))
    if not record:
        return None
    if record.request_hash != _request_hash(request, body):
        raise ValueError("同一 Idempotency-Key 不能用于不同接口、资源或请求体")
    return record.response_body


def _cache(db: Session, tenant_id: str, key: str, request: Request, body: object, response: dict) -> None:
    db.add(IdempotencyRecord(tenant_id=tenant_id, idempotency_key=key, request_hash=_request_hash(request, body), status_code=200, response_body=response))
    db.commit()


def _health_response() -> JSONResponse:
    database = "ok"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"
    payload = {
        "status": "ok" if database == "ok" else "degraded",
        "service": "companion-profile-engine",
        "version": __version__,
        "services": {"application": "ok", "database": database},
    }
    return JSONResponse(status_code=200 if database == "ok" else 503, content=payload)


@app.get("/health", tags=["system"])
def health() -> JSONResponse:
    return _health_response()


@app.get("/livez", tags=["system"], include_in_schema=False)
def liveness() -> dict:
    return {"status": "ok", "service": "companion-profile-engine", "version": __version__}


@app.get("/readyz", tags=["system"], include_in_schema=False)
def readiness() -> JSONResponse:
    return _health_response()


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    settings = get_settings()
    target = "/demo" if settings.demo_features_active else ("/docs" if settings.api_docs_active else "/health")
    return RedirectResponse(url=target, status_code=307)


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page() -> HTMLResponse:
    if not get_settings().demo_features_active:
        raise HTTPException(status_code=404, detail="Demo 功能未启用")
    html = files("profile_engine").joinpath("static/demo.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/rules", response_class=HTMLResponse, include_in_schema=False)
def rules_page() -> HTMLResponse:
    if not get_settings().demo_features_active:
        raise HTTPException(status_code=404, detail="规则工作台未启用")
    html = files("profile_engine").joinpath("static/rules.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/v1/profiles:init", tags=["profiles"])
def initialize(body: ProfileInitRequest, request: Request, tenant_id: str = Depends(auth_context),
               idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, request, body)
    if cached: return cached
    pack = current_pack(request, db)
    response = init_profile(db, tenant_id, body, pack, request.state.request_id, idem)
    _cache(db, tenant_id, idem, request, body, response)
    return response


@app.get("/v1/profiles/{user_id}", tags=["profiles"])
def read_profile(user_id: str, tenant_id: str = Depends(auth_context), db: Session = Depends(get_db)) -> dict:
    return get_profile(db, tenant_id, user_id)


@app.post("/v1/profiles/{user_id}/messages:ingest", tags=["messages"])
def ingest(user_id: str, body: MessageIngestRequest, request: Request, tenant_id: str = Depends(auth_context),
           idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, request, body)
    if cached: return cached
    pack = current_pack(request, db)
    response = ingest_message(db, tenant_id, user_id, body, pack, request.state.request_id, idem)
    _cache(db, tenant_id, idem, request, body, response)
    return response


@app.get("/v1/profiles/{user_id}/explain", tags=["profiles"])
def explain(user_id: str, field: str | None = Query(default=None), tenant_id: str = Depends(auth_context),
            db: Session = Depends(get_db)) -> dict:
    return explain_profile(db, tenant_id, user_id, field)


@app.post("/v1/profiles/{user_id}:correct", tags=["profiles"])
def correct(user_id: str, body: CorrectionRequest, request: Request, tenant_id: str = Depends(auth_context),
            idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, request, body)
    if cached: return cached
    response = correct_profile(db, tenant_id, user_id, body, current_pack(request, db), request.state.request_id, idem)
    _cache(db, tenant_id, idem, request, body, response)
    return response


@app.post("/v1/profiles/{user_id}:set-enneagram", tags=["profiles"])
def set_enneagram(
    user_id: str,
    body: SetEnneagramRequest,
    request: Request,
    tenant_id: str = Depends(auth_context),
    idem: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
) -> dict:
    cached = _cached(db, tenant_id, idem, request, body)
    if cached:
        return cached
    response = set_enneagram_profile(
        db,
        tenant_id,
        user_id,
        body,
        current_pack(request, db),
        request.state.request_id,
        idem,
    )
    _cache(db, tenant_id, idem, request, body, response)
    return response


@app.post("/v1/profiles/{user_id}:forget", tags=["profiles"])
def forget(user_id: str, body: ForgetRequest, request: Request, tenant_id: str = Depends(auth_context),
           idem: str = Depends(idempotency_key), db: Session = Depends(get_db)) -> dict:
    cached = _cached(db, tenant_id, idem, request, body)
    if cached: return cached
    response = forget_profile(db, tenant_id, user_id, body, current_pack(request, db), request.state.request_id, idem)
    _cache(db, tenant_id, idem, request, body, response)
    return response


@app.post(
    "/v1/profiles/{user_id}:reset",
    tags=["profiles"],
    summary="重置测试用户画像",
    description="删除该租户下指定用户的画像、对话、记忆与运行状态，并创建一份新的空白画像。必须显式确认且支持幂等重试。",
)
def reset_profile(
    user_id: str,
    body: ResetProfileRequest,
    request: Request,
    tenant_id: str = Depends(auth_context),
    idem: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
) -> dict:
    if not get_settings().profile_reset_active:
        raise HTTPException(status_code=404, detail="画像重置功能未启用")
    cached = _cached(db, tenant_id, idem, request, body)
    if cached:
        return cached

    existing = db.scalar(select(User).where(
        User.tenant_id == tenant_id,
        User.tenant_user_id == user_id,
    ))
    if existing:
        # A bulk delete lets the database enforce the declared ON DELETE
        # CASCADE relationships without loading sensitive child records.
        db.execute(delete(User).where(User.id == existing.id))
        db.flush()

    result = init_profile(
        db,
        tenant_id,
        ProfileInitRequest(
            tenant_user_id=user_id,
            display_name=body.display_name,
            consent=Consent(profile=True, sensitive_inference=False),
        ),
        current_pack(request, db),
        request.state.request_id,
        f"reset-init-{idem}",
    )
    response = {
        "request_id": request.state.request_id,
        "reset": True,
        "profile_version": result["profile_version"],
        "profile": result["profile"],
        "rule_pack": result["rule_pack"],
    }
    _cache(db, tenant_id, idem, request, body, response)
    return response


@app.get("/v1/rule-packs/current", tags=["rules"])
def rule_pack_current(request: Request, tenant_id: str = Depends(auth_context), db: Session = Depends(get_db)) -> dict:
    pack = current_pack(request, db)
    return {"version": pack.version, "sha256": pack.sha256, "status": pack.status,
            "validation_report": pack.validation_report, "published_at": pack.published_at}


@app.get("/v1/capabilities", tags=["system"])
def capabilities(request: Request, tenant_id: str = Depends(auth_context), db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    pack = current_pack(request, db)
    schema_version = pack.canonical_json.get("schema", {}).get("schema_version")
    return {
        "service": "companion-profile-engine",
        "service_version": __version__,
        "api_version": "v1",
        "tenant_id": tenant_id,
        "profile_schema_version": schema_version,
        "rule_pack": {"version": pack.version, "sha256": pack.sha256, "status": pack.status},
        "model_config": public_model_options(),
        "features": {
            "profile_versions": True,
            "idempotent_writes": True,
            "profile_explanations": True,
            "explicit_corrections": True,
            "forget_requests": True,
            "enneagram_requires_explicit_source": True,
            "profile_reset": settings.profile_reset_active,
        },
        "limits": {
            "requests_per_minute": settings.rate_limit_per_minute,
            "message_characters": 4000,
            "recent_turns": 12,
        },
    }
