from kairo_verify import show
from rigs import anyllm

out = anyllm.call(
    max_tokens=256,
    messages=[{"role": "user", "content": "List three fruits, then write END."}],
    stop_sequences=["END"],
)
show("client result", out.response.model_dump() if out.response is not None else repr(out.error))
show("forwarded to upstream", out.forwarded)
