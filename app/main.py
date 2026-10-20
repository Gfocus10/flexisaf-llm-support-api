"""FastAPI Support Assistant API. Interactive docs at /docs (Swagger) and /redoc."""
import uuid
from functools import lru_cache

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI

from .config import Settings, get_settings
from .llm_service import AppError, LLMService
from .logging_setup import get_logger
from .schemas import ErrorResponse, SupportRequest, SupportResponse

app = FastAPI(
    title="Reliable LLM Support Assistant API",
    version="1.0.0",
    description="Answers support questions with an LLM, with validation, timeouts, retries, fallback and cost logging.",
)
log = get_logger()


@lru_cache
def _service() -> LLMService:
    s = get_settings()
    # max_retries=0 turns off the SDK's own retries so Tenacity is the single source of retry behaviour.
    client = AsyncOpenAI(api_key=s.api_key or "missing", base_url=s.base_url,
                         timeout=s.timeout_seconds, max_retries=0)
    return LLMService(client, s)


def get_service() -> LLMService:
    return _service()


def _error(status: int, error: str, message: str, request_id: str | None, headers=None) -> JSONResponse:
    body = ErrorResponse(error=error, message=message, request_id=request_id).model_dump()
    return JSONResponse(status_code=status, content=body, headers=headers)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return _error(exc.status_code, exc.error, exc.message, getattr(request.state, "request_id", None), headers)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body") or "body"
    log.info("invalid_input", extra={"fields": {"field": field, "detail": first["msg"]}})
    return _error(422, "invalid_input", f"Invalid value for '{field}': {first['msg']}",
                  getattr(request.state, "request_id", None))


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    log.error("unexpected_error", extra={"fields": {"error_type": type(exc).__name__}})
    return _error(500, "internal_error", "Something went wrong on our side. Please try again later.",
                  getattr(request.state, "request_id", None))


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


_ERRORS = {
    400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
    500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@app.post("/v1/support/ask", response_model=SupportResponse, responses=_ERRORS, tags=["support"])
async def ask(body: SupportRequest, request: Request, svc: LLMService = Depends(get_service)):
    """Return a complete LLM-generated answer to a support question."""
    return await svc.ask(body, request.state.request_id)


@app.post("/v1/support/stream", responses=_ERRORS, tags=["support"],
          response_class=StreamingResponse)
async def stream(body: SupportRequest, request: Request, svc: LLMService = Depends(get_service)):
    """Stream the answer as Server-Sent Events. Each `data:` line is a JSON-encoded text chunk;
    the stream ends with `event: done`, or `event: error` if it fails part-way."""
    rid = request.state.request_id
    events, model, used_fallback, retries, start = await svc.open_stream(body, rid)
    headers = {"X-Model-Used": model, "Cache-Control": "no-cache"}
    return StreamingResponse(svc.stream_events(events, model, used_fallback, retries, rid, start),
                             media_type="text/event-stream", headers=headers)
