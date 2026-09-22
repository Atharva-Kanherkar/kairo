#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def texts(body):
    result = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            result.append(content)
        elif isinstance(content, list):
            result.extend(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--port", type=int, default=19311)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length", "0")))
            body = json.loads(raw)
            with open(args.capture, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "path": self.path,
                    "body_raw": raw.decode(),
                }, separators=(",", ":")) + "\n")

            seen = texts(body)
            discriminator = seen[-1].replace(" ", "-").lower() if seen else "empty"
            response_id = f"chatcmpl-{discriminator}"
            response = {
                "id": response_id,
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model", "model/chat"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "SEEN:" + "|".join(seen)},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            encoded = json.dumps(response, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
