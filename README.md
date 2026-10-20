# Reliable LLM Support Assistant API

A FastAPI backend that answers customer support questions with a large language model (LLM). It is built for reliability: request validation, timeouts, exponential backoff retries, a fallback model, clear error handling, streaming, and structured cost and usage logging.

It uses the official OpenAI Python SDK and works with OpenAI or any OpenAI-compatible provider. It was developed and tested against the Google Gemini free tier.

## Contents

1. [Features](#features)
2. [Quick start](#quick-start)
3. [Configuration](#configuration)
4. [API reference](#api-reference)
5. [How reliability is handled](#how-reliability-is-handled)
6. [Testing and evidence](#testing-and-evidence)
7. [Provider notes (Gemini)](#provider-notes-gemini)
8. [Cost assumptions](#cost-assumptions)
9. [Limitations](#limitations)
10. [Project structure](#project-structure)

## Features

1. Documented POST endpoints for full answers and for streaming (Swagger UI at /docs).
2. Request and response validation with Pydantic.
3. API key read from an environment variable, never from code.
4. Per-request timeout and retries with exponential backoff (Tenacity).
5. Automatic fallback to a cheaper backup model.
6. Clear HTTP status codes and user-friendly error messages for every failure type.
7. Structured JSON application log and a CSV log of tokens, estimated cost, latency, retries and status.
8. A sample client that tests successful and failing requests.

## Quick start

Requirements: Python 3.10 or newer.

1. Create a virtual environment and install the dependencies.

Windows (Git Bash):

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create your environment file and add your key.

```bash
cp .env.example .env
```

Open .env and set the values described in [Configuration](#configuration). The .env file is git-ignored, so your key is never committed.

3. Start the server.

```bash
uvicorn app.main:app --port 8000
```

4. Open the interactive documentation at http://127.0.0.1:8000/docs, or check that the service is running:

```bash
curl http://127.0.0.1:8000/health
```

Expected output:

```json
{"status":"ok"}
```

5. Run the sample client in a second terminal (successful, invalid and streaming requests):

```bash
python client/sample_client.py
```

## Configuration

All settings are environment variables, normally set in .env. Example for the Gemini free tier, which is the setup used for testing:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
PRIMARY_MODEL=gemini-3.6-flash
FALLBACK_MODEL=gemini-3.5-flash-lite
REQUEST_TIMEOUT_SECONDS=20
MAX_RETRIES=2
```

A free Gemini key is available at https://aistudio.google.com/apikey. No billing is needed for the free tier.

| Variable | Purpose | Default |
|---|---|---|
| LLM_PROVIDER | gemini, openai, or custom (any OpenAI-compatible API) | gemini |
| GEMINI_API_KEY, OPENAI_API_KEY, LLM_API_KEY | API key for the chosen provider | none |
| LLM_BASE_URL | Base URL, required for custom providers | provider preset |
| PRIMARY_MODEL | Model tried first | provider preset |
| FALLBACK_MODEL | Cheaper model used when the primary keeps failing | provider preset |
| REQUEST_TIMEOUT_SECONDS | Timeout for each model call | 20 |
| MAX_RETRIES | Maximum attempts per model (2 means the first try plus one retry) | 3 |
| RETRY_MIN_WAIT_SECONDS, RETRY_MAX_WAIT_SECONDS | Exponential backoff bounds | 1 and 8 |
| PRIMARY_INPUT_PRICE_PER_M and the matching OUTPUT, FALLBACK variables | Price in USD per million tokens, used for cost estimates | provider preset |

For OpenAI, set LLM_PROVIDER=openai and OPENAI_API_KEY. For other providers, set LLM_PROVIDER=custom with LLM_API_KEY, LLM_BASE_URL, PRIMARY_MODEL and FALLBACK_MODEL.

## API reference

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check |
| POST | /v1/support/ask | Returns a complete answer as JSON |
| POST | /v1/support/stream | Streams the answer as Server-Sent Events (see docs/streaming.md) |

### Request body

| Field | Type | Rules | Default |
|---|---|---|---|
| question | string | required, 3 to 2000 characters after trimming | none |
| temperature | number | 0 to 1 | 0.2 |
| max_output_tokens | integer | 16 to 2000 | 800 |

### Example request

```bash
curl -X POST http://127.0.0.1:8000/v1/support/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How do I reset my password?"}'
```

### Example response (HTTP 200)

```json
{
  "answer": "To reset your password, please follow these steps: ...",
  "model_used": "gemini-3.6-flash",
  "used_fallback": false,
  "truncated": false,
  "retry_count": 0,
  "input_tokens": 54,
  "output_tokens": 139,
  "estimated_cost_usd": 0,
  "latency_ms": 5661,
  "request_id": "cb91a7897c9c"
}
```

used_fallback is true when the backup model answered. truncated is true when the model stopped because it reached max_output_tokens, so the answer is incomplete. request_id also appears in the X-Request-ID response header and in the logs.

### Streaming example

```bash
curl -N -X POST http://127.0.0.1:8000/v1/support/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "How can I track my order?"}'
```

### Error responses

Every error has the same shape:

```json
{
  "error": "invalid_input",
  "message": "Invalid value for 'question': String should have at least 3 characters",
  "request_id": "05bf3572eb26"
}
```

| HTTP status | error | Cause |
|---|---|---|
| 422 | invalid_input | Invalid or malformed request |
| 429 | rate_limited | Provider rate limit (a Retry-After header is included) |
| 500 | service_misconfigured | Invalid API key or no permission |
| 500 | internal_error | Unexpected server error (details are never exposed) |
| 502 | bad_model_response | Empty or unusable model reply |
| 503 | service_unavailable | Provider down, unreachable, or out of quota |
| 504 | timeout | Provider took too long |

## How reliability is handled

1. Validation: bad input is rejected with HTTP 422 before any LLM call (app/schemas.py).
2. Timeout: REQUEST_TIMEOUT_SECONDS applies to every model call.
3. Retries: Tenacity retries temporary errors with exponential backoff. These are rate limits, timeouts, connection errors, 5xx errors and empty replies. The SDK's own retries are turned off so there is a single retry layer.
4. Fallback: if the primary model keeps failing with a temporary error, or the model is not found, the same request goes to FALLBACK_MODEL. Authentication errors skip fallback because another model cannot fix a bad key.
5. Truncation: if the model hits max_output_tokens, the API still returns 200 with truncated set to true and logs an answer_truncated warning.
6. Logging: logs/app.log holds one JSON line per event, and logs/usage_log.csv holds one row per request (model, tokens, cost, latency, retries, status), including failures.
7. Safe errors: user-facing messages never contain internal details or keys. Full details go to the log only.

## Testing and evidence

Testing was done manually. I ran the server, used the sample client (client/sample_client.py) and the Swagger UI, changed .env to trigger each failure, and saved the output and matching log lines in the evidence folder. All tests ran on 20 October 2026 against the Gemini free tier.

Successful requests: evidence/client_run_success.txt shows 7 of 7 cases passing on gemini-3.6-flash with no retries. The token, cost and latency log is in evidence/usage_log_sample.csv, and screenshots are in evidence/screenshots/.

Failure scenarios tested live:

| Scenario | How it was triggered | Observed result | Evidence |
|---|---|---|---|
| Invalid input | Empty, blank, over-long and out-of-range values, missing field | HTTP 422, the model is never called | client_run_success.txt, screenshots/swagger_422_invalid.png |
| Authentication error | Wrong API key | HTTP 500 service_misconfigured, no retries, no fallback | failure_auth_error.txt |
| Timeout | REQUEST_TIMEOUT_SECONDS=0.001 | Each model retried once, then HTTP 504 | failure_timeout.txt, app_log_timeout.txt |
| Primary model unavailable | PRIMARY_MODEL set to a model that does not exist | Provider returned 404, no retry, fallback answered with HTTP 200 | failure_bad_model_fallback.txt, app_log_bad_model_fallback.txt |
| Connection failure | LLM_BASE_URL pointed at an unreachable address | Both models retried, then HTTP 503 | failure_connection_error.txt, app_log_connection_error.txt |

Real provider problems also occurred during testing and were handled correctly. With gemini-3.8-flash, Gemini returned a server error and then timeouts, the code retried, and the fallback model answered (evidence/app_log_provider_errors.txt). A request with max_output_tokens=50 on a reasoning model was retried after an empty reply and then returned a cut-off answer flagged with truncated: true.

Every scenario, including those not triggered live, is listed with its expected behaviour in docs/FAILURE_SCENARIOS.md. Rate limiting (HTTP 429) could not be triggered on demand, so that path is documented from the code and was not exercised live.

A Postman collection is included at docs/postman_collection.json for convenience. The evidence above was gathered with the sample client and the Swagger UI, not Postman.

## Provider notes (Gemini)

1. A bad API key returns HTTP 400 (INVALID_ARGUMENT, "Please pass a valid API key") rather than 401. The code recognises it by the text "api key" in the error and returns 500 service_misconfigured. This depends on the provider's wording.
2. An unknown model returns HTTP 404, which triggers the fallback.
3. gemini-3.8-flash returned server errors and timeouts during testing, so gemini-3.6-flash is the primary model used. This may be temporary.
4. Gemini 3.x models are reasoning models. Thinking can use up max_output_tokens, so very small limits give empty or cut-off answers. Use 500 or more.
5. Reported output_tokens appeared to leave out thinking tokens, so token counts for truncated answers can look too small.
6. Google describes its OpenAI-compatible endpoint as beta, so behaviour may change.

## Cost assumptions

1. Cost per request = (input_tokens x input_price + output_tokens x output_price) / 1,000,000, using the token counts returned by the API.
2. The Gemini free tier costs nothing, so estimated_cost_usd shows 0 in the logs. The free tier is rate limited, which is exactly when retries and fallback matter, and free-tier content may be used by Google to improve its products.
3. docs/cost_estimation.xlsx projects monthly cost on the paid tier. Prices were checked on 20 October 2026 on Google's pricing page: gemini-3.6-flash costs 0.75 USD per million input tokens and 3.75 USD per million output tokens until 31 December 2026 (then 1.50 and 7.50), and gemini-3.5-flash-lite costs 0.30 and 2.50. The assumptions are 55 input tokens and 120 output tokens per request, with 5 percent of traffic served by the fallback. At 1,000 requests per day this is about 14.48 USD per month.
4. Output prices include thinking tokens, but the API's reported output_tokens appeared to omit them, so real paid costs may be higher than estimated.
5. Failed attempts return no usage data, so they are not in the logged cost, although a timed-out request may still be billed by the provider.
6. In the spreadsheet, blue cells are inputs and the rest are formulas. Regenerate it with the command below. Model names and prices change often, so confirm them in your provider's documentation.

```bash
python docs/make_cost_spreadsheet.py
```

## Limitations

1. There is no conversation memory. Each request is independent.
2. The streaming endpoint cannot change its HTTP status after the first chunk, so a mid-stream failure arrives as an error event. Truncation is not flagged on streams.
3. Retry waits happen inside the request. Typical latency during testing was 1 to 10 seconds, but the worst case is roughly timeout x attempts x 2 models (about 80 seconds with the settings above).
4. Only the first validation error is reported when several fields are invalid, and a malformed JSON body gives the generic message "JSON decode error".
5. Detecting a bad key from the error text is provider specific. Other providers may need a different rule.

## Project structure

```text
app/
  config.py          settings and provider presets
  schemas.py         request and response models
  llm_service.py     LLM calls, retries, fallback, cost, streaming
  logging_setup.py   JSON log and CSV usage log
  main.py            FastAPI routes and error handlers
client/
  sample_client.py   sample client (success, failure and streaming cases)
docs/
  FAILURE_SCENARIOS.md
  streaming.md
  cost_estimation.xlsx
  make_cost_spreadsheet.py
  postman_collection.json
evidence/            test outputs, logs and screenshots
logs/                runtime logs (git-ignored)
.env.example
requirements.txt
```
