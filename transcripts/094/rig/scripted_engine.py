"""Deterministic scripted LLMEngine for Dynamo's public backend API.

The frontend under test (dynamo.frontend) does all OpenAI/Anthropic/Responses
handling, chat templating, tokenization, and tool/reasoning parsing. This
engine only stands in for the GPU: it records exactly what it was forwarded
(token ids plus the decoded prompt) and replies with the token ids of a
scripted completion chosen by a `SCRIPT:<name>` marker in the prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import re
import time

from transformers import AutoTokenizer

from dynamo.common.backend.engine import EngineConfig, LLMEngine, LlmRegistration
from dynamo.common.backend.health_check import build_health_check_payload
from dynamo.common.backend.run import run
from dynamo.common.backend.worker import WorkerConfig

CAPTURE_DIR = os.environ.get("CAPTURE_DIR", "/rig/captures")
SCRIPT_DIR = os.environ.get("SCRIPT_DIR", "/rig/scripts")
_counter = itertools.count(1)


class ScriptedEngine(LLMEngine):
    def __init__(self, model: str, served: str, tokens_per_chunk: int):
        self.model = model
        self.served = served
        self.tokens_per_chunk = tokens_per_chunk
        self.tok = AutoTokenizer.from_pretrained(model)

    @classmethod
    async def from_args(cls, argv=None):
        p = argparse.ArgumentParser()
        p.add_argument("--model", default="Qwen/Qwen3-0.6B")
        p.add_argument("--served-model-name", default=None)
        p.add_argument("--tool-call-parser", default=None)
        p.add_argument("--reasoning-parser", default=None)
        p.add_argument("--tokens-per-chunk", type=int, default=1)
        p.add_argument("--discovery-backend", default="file")
        a = p.parse_args(argv)
        served = a.served_model_name or a.model
        eng = cls(a.model, served, a.tokens_per_chunk)
        cfg = WorkerConfig(
            namespace="dynamo",
            component="backend",
            endpoint="generate",
            model_name=a.model,
            served_model_name=served,
            discovery_backend=a.discovery_backend,
            tool_call_parser=a.tool_call_parser,
            reasoning_parser=a.reasoning_parser,
            enable_kv_routing=False,
        )
        return eng, cfg

    async def start(self, worker_id: int) -> EngineConfig:
        return EngineConfig(
            model=self.model,
            served_model_name=self.served,
            llm=LlmRegistration(
                context_length=40960,
                kv_cache_block_size=16,
                total_kv_blocks=10000,
                max_num_seqs=64,
                max_num_batched_tokens=40960,
            ),
        )

    async def health_check_payload(self):
        return build_health_check_payload(bos_token_id=self.tok.bos_token_id or 0)

    async def generate(self, request, context):
        n = next(_counter)
        token_ids = list(request.get("token_ids") or [])
        prompt = self.tok.decode(token_ids, skip_special_tokens=False)
        names = re.findall(r"SCRIPT:([A-Za-z0-9_\-]+)", prompt)
        name = names[-1] if names else None
        # After a tool round trip (legacy XML function_results injected after the
        # marker), play "<name>-after" if it exists.
        if name and os.path.exists(os.path.join(SCRIPT_DIR, name + "-after.txt")):
            if "<stdout>" in prompt[prompt.rfind("SCRIPT:" + name):]:
                name = name + "-after"
        text = ""
        if name:
            with open(os.path.join(SCRIPT_DIR, name + ".txt")) as f:
                text = f.read()
        out_ids = self.tok.encode(text, add_special_tokens=False) if text else []
        # Deterministic continuation: on a migrated retry the frontend appends the
        # tokens already generated to the prompt. Resume after the longest suffix of
        # the prompt that equals a prefix of the scripted completion.
        resume = 0
        for k in range(min(len(out_ids), len(token_ids)), 0, -1):
            if token_ids[-k:] == out_ids[:k]:
                resume = k
                break
        full_ids = out_ids
        out_ids = out_ids[resume:]
        die_after = None
        flag = os.path.join(SCRIPT_DIR, "DIE_ONCE")
        if os.path.exists(flag):
            die_after = int(open(flag).read().strip() or "5")
            os.remove(flag)
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        with open(os.path.join(CAPTURE_DIR, f"forwarded-{time.time_ns()}-{n:04d}.json"), "w") as f:
            json.dump(
                {
                    "seq": n,
                    "time": time.time(),
                    "script": name,
                    "request": request,
                    "decoded_prompt": prompt,
                    "scripted_completion": text,
                    "completion_token_ids": out_ids,
                    "resume_from": resume,
                    "die_after": die_after,
                    "pid": os.getpid(),
                },
                f,
                indent=1,
                default=str,
            )
        step = self.tokens_per_chunk
        delay = float(os.environ.get("TOKEN_DELAY", "0.002"))
        sent = 0; stopped = False
        try:
            for i in range(0, len(out_ids), step):
                if context.is_stopped():
                    stopped = True
                    break
                if die_after is not None and sent >= die_after:
                    with open(os.path.join(CAPTURE_DIR, "lifecycle.jsonl"), "a") as f:
                        f.write(json.dumps({"seq": n, "event": "worker_exit", "pid": os.getpid(), "sent": sent, "t": time.time()}) + "\n")
                    os._exit(1)
                yield {"token_ids": out_ids[i : i + step], "index": 0}
                sent += len(out_ids[i : i + step])
                await asyncio.sleep(delay)
        finally:
            with open(os.path.join(CAPTURE_DIR, "lifecycle.jsonl"), "a") as f:
                f.write(json.dumps({"seq": n, "script": name, "planned": len(out_ids), "sent": sent, "stopped_seen": stopped, "t": time.time()}) + "\n")
        yield {
            "token_ids": [],
            "index": 0,
            "finish_reason": "stop",
            "completion_usage": {
                "prompt_tokens": len(token_ids),
                "completion_tokens": len(out_ids),
                "total_tokens": len(token_ids) + len(out_ids),
            },
        }

    async def cleanup(self):
        pass


if __name__ == "__main__":
    run(ScriptedEngine)
