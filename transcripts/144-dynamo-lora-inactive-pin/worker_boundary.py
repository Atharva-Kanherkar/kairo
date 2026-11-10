"""Exercise the real LoRAState.resolve_request with stubbed import names.

Only the imported names are stubbed (vLLM's LoRARequest, LoRAInfo,
InvalidArgument). The resolve_request body under test is the verbatim file.
"""

import importlib.util
import sys
import types
from dataclasses import dataclass


@dataclass
class LoRARequest:
    lora_name: str
    lora_int_id: int
    lora_path: str


@dataclass
class LoRAInfo:
    id: int
    path: str


class InvalidArgument(Exception):
    pass


def install_stubs():
    vllm = types.ModuleType("vllm")
    vllm_lora = types.ModuleType("vllm.lora")
    vllm_request = types.ModuleType("vllm.lora.request")
    vllm_request.LoRARequest = LoRARequest
    vllm.lora = vllm_lora
    vllm_lora.request = vllm_request

    dynamo = types.ModuleType("dynamo")
    common = types.ModuleType("dynamo.common")
    common_lora = types.ModuleType("dynamo.common.lora")
    manager = types.ModuleType("dynamo.common.lora.manager")
    manager.LoRAInfo = LoRAInfo
    llm = types.ModuleType("dynamo.llm")
    exceptions = types.ModuleType("dynamo.llm.exceptions")
    exceptions.InvalidArgument = InvalidArgument

    for name, module in {
        "vllm": vllm,
        "vllm.lora": vllm_lora,
        "vllm.lora.request": vllm_request,
        "dynamo": dynamo,
        "dynamo.common": common,
        "dynamo.common.lora": common_lora,
        "dynamo.common.lora.manager": manager,
        "dynamo.llm": llm,
        "dynamo.llm.exceptions": exceptions,
    }.items():
        sys.modules[name] = module


def load_lora_state(path):
    spec = importlib.util.spec_from_file_location("dynamo_lora_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    install_stubs()
    module = load_lora_state("components/src/dynamo/vllm/lora_state.py")
    state = module.LoRAState()
    state.loaded_loras = {"busy": LoRAInfo(id=1, path="/models/busy")}

    print("worker does not host 'idle':")
    try:
        state.resolve_request(
            "idle", base_model_names=("base",), lora_enabled=True
        )
        print("  UNEXPECTED: returned a LoRARequest")
    except InvalidArgument as exc:
        print(f"  InvalidArgument: {exc}")

    state.loaded_loras["idle"] = LoRAInfo(id=2, path="/models/idle")
    request = state.resolve_request(
        "idle", base_model_names=("base",), lora_enabled=True
    )
    print("worker hosts 'idle':")
    print(f"  LoRARequest(lora_name={request.lora_name}, id={request.lora_int_id})")


if __name__ == "__main__":
    main()
