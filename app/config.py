"""Settings loaded from environment variables. No secrets live in code."""
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: Optional[str]
    api_style: str  # "responses" (OpenAI) or "chat" (chat.completions, used by OpenAI-compatible providers)
    api_key: str
    primary_model: str
    fallback_model: str
    timeout_seconds: float
    max_retries: int
    retry_min_wait: float
    retry_max_wait: float
    # model name -> (input price, output price) in USD per 1M tokens
    prices: dict


PROVIDERS = {
    # name: (key env var, base_url, api_style, primary model, fallback model, primary prices, fallback prices)
    "openai": ("OPENAI_API_KEY", None, "responses", "gpt-4.1-mini", "gpt-4.1-nano", (0.40, 1.60), (0.10, 0.40)),
    # Gemini free tier via Google's OpenAI-compatible endpoint. Free tier means 0 cost, but it is rate limited.
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/", "chat",
               "gemini-3.8-flash", "gemini-3.5-flash-lite", (0.0, 0.0), (0.0, 0.0)),
    # Any other OpenAI-compatible provider: set LLM_API_KEY, LLM_BASE_URL, PRIMARY_MODEL, FALLBACK_MODEL.
    "custom": ("LLM_API_KEY", None, "chat", "", "", (0.0, 0.0), (0.0, 0.0)),
}


def get_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {sorted(PROVIDERS)}, got {provider!r}")
    key_env, base_url, style, primary_default, fallback_default, p_prices, f_prices = PROVIDERS[provider]
    primary = os.getenv("PRIMARY_MODEL") or primary_default
    fallback = os.getenv("FALLBACK_MODEL") or fallback_default
    return Settings(
        provider=provider,
        base_url=os.getenv("LLM_BASE_URL") or base_url,
        api_style=style,
        api_key=os.getenv(key_env, "") or os.getenv("LLM_API_KEY", ""),
        primary_model=primary,
        fallback_model=fallback,
        timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        retry_min_wait=float(os.getenv("RETRY_MIN_WAIT_SECONDS", "1")),
        retry_max_wait=float(os.getenv("RETRY_MAX_WAIT_SECONDS", "8")),
        prices={
            primary: (float(os.getenv("PRIMARY_INPUT_PRICE_PER_M", p_prices[0])),
                      float(os.getenv("PRIMARY_OUTPUT_PRICE_PER_M", p_prices[1]))),
            fallback: (float(os.getenv("FALLBACK_INPUT_PRICE_PER_M", f_prices[0])),
                       float(os.getenv("FALLBACK_OUTPUT_PRICE_PER_M", f_prices[1]))),
        },
    )
