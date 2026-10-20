# Failure scenarios and expected behaviour

Expected behaviour for each failure, with the result observed when it was tested. Tests were run manually against the Gemini free tier on 20 October 2026 (primary gemini-3.6-flash, fallback gemini-3.5-flash-lite, REQUEST_TIMEOUT_SECONDS=20 unless noted, MAX_RETRIES=2 attempts per model). Evidence files are in evidence/.

Status meanings: Tested means I triggered it and kept the output. Observed means it happened during testing without me setting it up deliberately. Code only means it is difficult to trigger on demand and is documented from how the code behaves.

| # | Scenario | Retried? | Fallback model? | Expected HTTP result | Status | Evidence |
|---|----------|----------|-----------------|----------------------|--------|----------|
| 1 | Invalid input: empty, blank, over 2000 characters, temperature out of range, missing field | No | No | 422 invalid_input, model never called | Tested | client_run_success.txt, screenshots/swagger_422_invalid.png |
| 2 | Malformed JSON body (for example curly quotes) | No | No | 422 invalid_input | Observed | seen in Swagger during testing |
| 3 | Authentication error (bad or revoked key) | No | No, another model cannot fix a key | 500 service_misconfigured, no key details in the message | Tested | failure_auth_error.txt, usage_log_sample.csv |
| 4 | Timeout on both models | Yes, one retry per model | Tried | 504 timeout | Tested | failure_timeout.txt, app_log_timeout.txt |
| 5 | Primary model not found | No | Yes | 200 from the fallback model, used_fallback true | Tested | failure_bad_model_fallback.txt, app_log_bad_model_fallback.txt |
| 6 | Network or connection failure on both models | Yes, one retry per model | Tried | 503 service_unavailable | Tested | failure_connection_error.txt, app_log_connection_error.txt |
| 7 | Provider server error (5xx) or timeout on the primary, fallback healthy | Yes | Yes | 200 from the fallback model | Observed | app_log_provider_errors.txt (gemini-3.8-flash) |
| 8 | Empty model reply (reasoning used all output tokens) | Yes | Yes if it keeps happening | 200 if a retry succeeds, otherwise 502 bad_model_response | Observed | retry_count 1 on a request with max_output_tokens=50 (the empty reply is the likely cause, since the retry path for it is the only one that could fire there) |
| 9 | Answer cut off by max_output_tokens | No | No | 200 with truncated true and an answer_truncated log warning | Tested | answer_truncated line in app_log_full.txt |
| 10 | Rate limit (429) that recovers | Yes, with backoff | No | 200, retry_count shows the retries | Code only | |
| 11 | Rate limit that persists on the primary | Yes, then gives up | Yes | 200 from the fallback model | Code only | |
| 12 | Rate limit on both models | Yes, both | Tried | 429 rate_limited with a Retry-After header | Code only | |
| 13 | Out of quota (a 429 that retrying cannot fix) | No | Tried once | 503 service_unavailable | Code only | |
| 14 | Unexpected internal exception | No | No | 500 internal_error, internal details hidden | Code only | |
| 15 | Failure while opening a stream | Yes | Yes | A normal HTTP error before streaming starts | Tested | the streaming section of each failure client run |
| 16 | Failure after streaming has started | No | No | An error event in the stream, because the 200 status was already sent | Code only | |

## Notes

1. Retry policy: MAX_RETRIES is the maximum number of attempts per model with exponential backoff (RETRY_MIN_WAIT_SECONDS to RETRY_MAX_WAIT_SECONDS, default 1 to 8 seconds). Only rate limits, timeouts, connection errors, 5xx errors and empty replies are retried.
2. With two attempts per model, a request that fails everywhere makes four attempts in total (two per model), and retry_count shows 2.
3. Gemini reports a bad API key as HTTP 400 INVALID_ARGUMENT instead of 401. The code recognises it from the "api key" text in the message and maps it to 500 service_misconfigured. Without this rule, it would have been reported as invalid input, which is how it first behaved in testing.
4. Every request, success or failure, writes one JSON log line and one row in logs/usage_log.csv with model, tokens, cost, latency, retry count and status. Per-model failures also write a model_failed log line with the provider's error text.
5. Error bodies always have the shape {"error": ..., "message": ..., "request_id": ...}. The request_id matches the X-Request-ID header and the logs.
6. Only the first validation error is reported when several fields are invalid.
