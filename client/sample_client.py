"""Sample API client. Runs successful and failing requests against a running server and prints a report.

Usage:  python client/sample_client.py [--base-url http://127.0.0.1:8000] [--save evidence/client_run.txt]
Start the server first:  uvicorn app.main:app --port 8000
"""
import argparse
import json
import sys

import httpx

CASES = [
    # name, path, payload, expected status codes
    ("success: normal question", "/v1/support/ask",
     {"question": "How do I reset my password?"}, {200}),
    ("success: custom settings", "/v1/support/ask",
     {"question": "What is your refund policy?", "temperature": 0.0, "max_output_tokens": 500}, {200}),
    ("failure: empty question", "/v1/support/ask", {"question": ""}, {422}),
    ("failure: whitespace only question",
     "/v1/support/ask", {"question": "     "}, {422}),
    ("failure: question too long", "/v1/support/ask",
     {"question": "x" * 2001}, {422}),
    ("failure: temperature out of range", "/v1/support/ask",
     {"question": "Hello there", "temperature": 9}, {422}),
    ("failure: missing question field", "/v1/support/ask", {}, {422}),
]


def run_stream(client: httpx.Client, base: str, out):
    out("\n== streaming: /v1/support/stream ==")
    with client.stream("POST", base + "/v1/support/stream", json={"question": "Explain how to track my order."}) as r:
        out(f"status={r.status_code} model={r.headers.get('X-Model-Used')}")
        if r.status_code != 200:
            out(r.read().decode())
            return r.status_code == 200
        text = ""
        for line in r.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                text += json.loads(line[6:])
            elif line.startswith("event: error"):
                out("stream error event received")
        out("streamed text: " + text[:200])
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--save", help="also write the report to this file")
    args = ap.parse_args()
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    passed = failed = 0
    with httpx.Client(timeout=120) as client:
        for name, path, payload, expected in CASES:
            try:
                r = client.post(args.base_url + path, json=payload)

            except httpx.TimeoutException as e:
                failed += 1
                out(f"[FAIL] {name}: client timed out ({e.__class__.__name__}). The server may still be retrying.")
                continue

            except httpx.HTTPError as e:
                out(f"[ERROR] {name}: cannot reach server ({e.__class__.__name__}). Is it running?")
                sys.exit(2)
            body = r.json()
            good = r.status_code in expected
            passed, failed = passed + good, failed + (not good)
            out(f"[{'PASS' if good else 'FAIL'}] {name}: HTTP {r.status_code} (expected {sorted(expected)})")
            if r.status_code == 200:
                out(f"        answer={body['answer'][:80]!r} model={body['model_used']} tokens={body['input_tokens']}+{body['output_tokens']} "
                    f"cost=${body['estimated_cost_usd']} retries={body['retry_count']} latency={body['latency_ms']}ms")
            else:
                out(f"        {body.get('error')}: {body.get('message')}")
        run_stream(client, args.base_url, out)
    out(f"\n{passed} passed, {failed} failed")
    if args.save:
        with open(args.save, "w") as f:
            f.write("\n".join(lines) + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
