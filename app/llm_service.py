"""LLM calls with timeout, retries (exponential backoff), fallback model, cost estimation."""
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import openai
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from .config import Settings
from .logging_setup import get_logger, log_request
from .schemas import SupportRequest, SupportResponse

SYSTEM_PROMPT = (
    "You are a helpful, concise customer support assistant. Answer the customer's question clearly. "
    "If you do not know something, say so and suggest contacting a human agent. Never invent order "
    "numbers, policies, or personal data."
)


class AppError(Exception):
    """An error already translated into something safe to show a user."""

    def __init__(self, status_code: int, error: str, message: str, retry_after: Optional[int] = None):
        self.status_code, self.error, self.message, self.retry_after = status_code, error, message, retry_after


class MalformedResponse(Exception):
    """The model call succeeded but returned no usable text."""


# Temporary problems worth retrying. insufficient_quota is a 429 that retrying cannot fix, handled below.
RETRYABLE = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
             openai.InternalServerError, MalformedResponse)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, openai.RateLimitError) and getattr(exc, "code", None) == "insufficient_quota":
        return False
    return isinstance(exc, RETRYABLE)


def translate_error(exc: BaseException) -> AppError:
    """Map an SDK or internal exception to a status code and a user-friendly message."""
    if isinstance(exc, openai.AuthenticationError) or isinstance(exc, openai.PermissionDeniedError):
        return AppError(500, "service_misconfigured", "The assistant is not configured correctly. Please contact support.")
    if isinstance(exc, openai.RateLimitError):
        if getattr(exc, "code", None) == "insufficient_quota":
            return AppError(503, "service_unavailable", "The assistant is temporarily unavailable. Please try again later.")
        return AppError(429, "rate_limited", "Too many requests right now. Please wait a moment and try again.", retry_after=10)
    if isinstance(exc, openai.APITimeoutError):
        return AppError(504, "timeout", "The assistant took too long to respond. Please try again.")
    if isinstance(exc, (openai.APIConnectionError, openai.InternalServerError)):
        return AppError(503, "service_unavailable", "The assistant is temporarily unavailable. Please try again shortly.")
    if isinstance(exc, MalformedResponse):
        return AppError(502, "bad_model_response", "The assistant returned an unusable reply. Please try again.")
    if isinstance(exc, openai.BadRequestError):
        # Some providers (Gemini) report a bad API key as 400 instead of 401.
        if "api key" in str(exc).lower():
            return AppError(500, "service_misconfigured", "The assistant is not configured correctly. Please contact support.")
        return AppError(400, "invalid_input", "Your question could not be processed. Please rephrase it and try again.")
    return AppError(500, "internal_error", "Something went wrong on our side. Please try again later.")


@dataclass
class Attempt:
    model: str
    retries: int = 0


def _usage(u) -> tuple[int, int]:
    """Read token counts from either API style (Responses: input/output_tokens, chat: prompt/completion_tokens)."""
    if u is None:
        return 0, 0
    return (getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", 0) or 0,
            getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", 0) or 0)


def _truncated(r) -> bool:
    """True when the model stopped because it hit the output token limit."""
    if getattr(r, "choices", None):  # chat.completions
        return r.choices[0].finish_reason == "length"
    return getattr(r, "status", None) == "incomplete"  # Responses API


class LLMService:
    def __init__(self, client, settings: Settings):
        # client is an AsyncOpenAI configured with max_retries=0, so Tenacity owns all retry logic.
        self.client = client
        self.s = settings
        self.log = get_logger()

    def _cost(self, model: str, in_tok: int, out_tok: int) -> float:
        pin, pout = self.s.prices.get(model, (0.0, 0.0))
        return (in_tok * pin + out_tok * pout) / 1_000_000

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.s.max_retries),
            wait=wait_exponential(
                multiplier=1, min=self.s.retry_min_wait, max=self.s.retry_max_wait),
            reraise=True,
        )

    async def _call_with_retries(self, fn, attempt: Attempt):
        """Run fn() with exponential backoff on temporary errors; records the retry count."""
        retrying = self._retrying()
        try:
            return await retrying(fn)
        finally:
            attempt.retries = retrying.statistics.get("attempt_number", 1) - 1

    async def _create(self, model: str, req: SupportRequest, stream: bool = False):
        """One provider call. Responses API for OpenAI, chat.completions for OpenAI-compatible providers (Gemini)."""
        if self.s.api_style == "chat":
            extra = {"stream_options": {
                "include_usage": True}} if stream else {}
            return await self.client.chat.completions.create(
                model=model, messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                       {"role": "user", "content": req.question}],
                temperature=req.temperature, max_tokens=req.max_output_tokens, stream=stream, **extra)
        return await self.client.responses.create(
            model=model, instructions=SYSTEM_PROMPT, input=req.question, temperature=req.temperature,
            max_output_tokens=req.max_output_tokens, stream=stream)

    @staticmethod
    def _text(r) -> str:
        if getattr(r, "choices", None):  # chat.completions shape
            return (r.choices[0].message.content or "").strip()
        return (getattr(r, "output_text", None) or "").strip()

    def _models(self):
        return [(self.s.primary_model, False), (self.s.fallback_model, True)]

    def _should_fallback(self, exc: BaseException) -> bool:
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return False  # the key is the problem, a different model will not help
        return _is_retryable(exc) or isinstance(exc, openai.NotFoundError)

    async def ask(self, req: SupportRequest, request_id: str) -> SupportResponse:
        start = time.perf_counter()
        total_retries = 0
        last_exc: Optional[BaseException] = None
        for model, is_fallback in self._models():
            attempt = Attempt(model)

            async def call(model=model):
                r = await self._create(model, req)
                text = self._text(r)
                if not text:
                    raise MalformedResponse("empty output")
                return r, text

            try:
                resp, text = await self._call_with_retries(call, attempt)
            except Exception as exc:  # noqa: BLE001 - translated below
                total_retries += attempt.retries
                last_exc = exc
                self.log.warning("model_failed", extra={"fields": {
                    "request_id": request_id, "model": model, "error_type": type(exc).__name__,
                    "error_message": str(exc)[:300],
                    "retries": attempt.retries, "will_fallback": (not is_fallback) and self._should_fallback(exc)}})
                if is_fallback or not self._should_fallback(exc):
                    break
                continue
            total_retries += attempt.retries
            usage = resp.usage
            in_tok, out_tok = _usage(usage)
            truncated = _truncated(resp)
            if truncated:
                self.log.warning("answer_truncated", extra={"fields": {
                    "request_id": request_id, "model": model, "max_output_tokens": req.max_output_tokens}})
            cost = self._cost(model, in_tok, out_tok)
            latency = int((time.perf_counter() - start) * 1000)
            log_request(request_id, model, is_fallback, in_tok,
                        out_tok, cost, latency, total_retries, "success")
            return SupportResponse(answer=text, model_used=model, used_fallback=is_fallback, truncated=truncated,
                                   retry_count=total_retries, input_tokens=in_tok, output_tokens=out_tok,
                                   estimated_cost_usd=round(cost, 6), latency_ms=latency, request_id=request_id)

        err = translate_error(last_exc)
        latency = int((time.perf_counter() - start) * 1000)
        log_request(request_id, "none", False, 0, 0, 0.0,
                    latency, total_retries, f"failed:{err.error}")
        raise err

    async def open_stream(self, req: SupportRequest, request_id: str):
        """Open a streaming call (with retries and fallback) before any bytes are sent to the client.
        Returns (event_stream, model, used_fallback, retries, start). Failures here become proper HTTP errors."""
        total_retries = 0
        start = time.perf_counter()
        last_exc: Optional[BaseException] = None
        for model, is_fallback in self._models():
            attempt = Attempt(model)

            async def call(model=model):
                return await self._create(model, req, stream=True)

            try:
                stream = await self._call_with_retries(call, attempt)
                return stream, model, is_fallback, total_retries + attempt.retries, start
            except Exception as exc:  # noqa: BLE001
                total_retries += attempt.retries
                last_exc = exc
                self.log.warning("model_failed", extra={"fields": {
                    "request_id": request_id, "model": model, "error_type": type(exc).__name__,
                    "error_message": str(exc)[:300], "retries": attempt.retries,
                    "will_fallback": (not is_fallback) and self._should_fallback(exc)}})
                if is_fallback or not self._should_fallback(exc):
                    break
        err = translate_error(last_exc)
        log_request(request_id, "none", False, 0, 0, 0.0, int((time.perf_counter() - start) * 1000),
                    total_retries, f"failed:{err.error}")
        raise err

    async def stream_events(self, stream, model, is_fallback, retries, request_id, start) -> AsyncIterator[str]:
        """Yield Server-Sent Events. Errors after the first byte are sent as an 'error' event."""
        in_tok = out_tok = 0
        status = "success"
        try:
            async for event in stream:
                if hasattr(event, "choices"):  # chat.completions chunk
                    if event.choices and event.choices[0].delta.content:
                        yield f"data: {json.dumps(event.choices[0].delta.content)}\n\n"
                    if getattr(event, "usage", None):
                        in_tok, out_tok = _usage(event.usage)
                elif event.type == "response.output_text.delta":
                    yield f"data: {json.dumps(event.delta)}\n\n"
                elif event.type == "response.completed":
                    in_tok, out_tok = _usage(event.response.usage)
            yield "event: done\ndata: [DONE]\n\n"
        except Exception as exc:  # noqa: BLE001
            err = translate_error(exc)
            status = f"failed_mid_stream:{err.error}"
            yield f"event: error\ndata: {err.message}\n\n"
        finally:
            cost = self._cost(model, in_tok, out_tok)
            log_request(request_id, model, is_fallback, in_tok, out_tok, cost,
                        int((time.perf_counter() - start) * 1000), retries, status)
