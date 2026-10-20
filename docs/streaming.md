# Streaming

Implemented at POST /v1/support/stream using Server-Sent Events. The request body is the same as /v1/support/ask.

1. The server opens the provider's stream first, with retries and fallback, before any bytes go to the client. So connection, authentication, timeout and rate-limit problems still return normal HTTP error codes (for example 500 or 504) and the model is shown in the X-Model-Used response header on success.
2. Each text chunk is sent as `data: "<json string>"`. JSON encoding keeps newlines and quotes inside the text safe, so clients should json.loads() the part after `data: `.
3. The stream ends with `event: done`. If a failure happens after the first chunk, the HTTP status is already 200, so the server sends `event: error` with a friendly message instead.
4. Token usage:
   For OpenAI (Responses API) it arrives in the final response.completed event.
   For Gemini and other chat.completions providers it arrives in a final chunk, because the request sets stream_options include_usage. This worked with Gemini during testing (54 input and 89 output tokens were logged for a streamed answer). If a provider ever omits it, tokens are logged as 0 and nothing breaks.
5. Token usage and cost are logged when the stream finishes. latency_ms covers the whole request, including connection time and any retries before the stream started.
6. Limitations: the truncated flag is not reported for streams, and a failure after the first chunk cannot change the HTTP status.

Sample client usage is in client/sample_client.py, which reads the stream line by line and joins the decoded chunks.
