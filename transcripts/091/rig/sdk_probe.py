import json, sys, os
from anthropic import Anthropic
import httpx

PORT = sys.argv[1]
PROBE = sys.argv[2]

# Minimal SSE client: hand the SDK a raw byte stream from the gateway.
def raw_stream(path="/v1/messages", body=None):
    return httpx.Response(200, content=open(os.environ["RAW_SSE"], "rb").read(),
                          headers={"content-type": "text/event-stream"})

client = Anthropic(api_key="not-used", http_client=httpx.Client(transport=httpx.MockTransport(
    lambda req: raw_stream())))

req = {"model": "claude-opus-4-6", "max_tokens": 1024,
       "messages": [{"role": "user", "content": "weather?"}],
       "tools": [{"name": "get_weather", "description": "d",
                  "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}]}

print(f"=== {PROBE} via anthropic {__import__('anthropic').__version__} SDK accumulating stream ===")
try:
    with client.messages.stream(**req) as stream:
        print("text:", repr(stream.text_stream.__self__ and "".join(stream.text_stream) if False else None))
        final = stream.get_final_message()
        print("STOP:", final.stop_reason)
        for b in final.content:
            print("  block:", b.type, getattr(b, "id", None), getattr(b, "name", None),
                  repr(getattr(b, "text", None)), getattr(b, "partial_json", None))
except Exception as e:
    print("RAISED:", type(e).__name__, e)
