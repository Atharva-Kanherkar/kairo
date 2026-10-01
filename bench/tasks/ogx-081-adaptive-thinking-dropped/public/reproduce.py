from kairo_verify import show
from rigs import ogx

base = {"model": ogx.MODEL, "max_tokens": 256, "messages": [{"role": "user", "content": "Plan a three-step migration."}]}
with ogx.Rig() as rig:
    for thinking in ({"type": "adaptive"}, {"type": "enabled", "budget_tokens": 1024}):
        status, body, raw, forwarded = rig.messages({**base, "thinking": thinking})
        show(f"thinking={thinking}: client HTTP {status}", raw)
        show("forwarded to upstream", forwarded[-1] if forwarded else "(no upstream call)")
