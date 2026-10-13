from kairo_verify import show
from rigs import anyllm

out = anyllm.call(messages=[
    {"role": "user", "content": "2+2?"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "Adding two and two gives four.", "signature": "sig-abc"},
        {"type": "text", "text": "4"},
    ]},
    {"role": "user", "content": "now 3+3"},
])
show("client result", out.response.model_dump() if out.response is not None else repr(out.error))
show("forwarded to upstream", out.forwarded)
