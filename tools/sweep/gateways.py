# Gateway adapters: how to stand each gateway up with its backend pointed at
# the sweep capture mock, and how to talk to its Anthropic and OpenAI ingresses.
#
# Every adapter supports "attach" mode: if something is already listening on
# the ingress port, the sweep uses it as-is and does not try to launch. That is
# the escape hatch for gateways whose configuration lives in a database or a
# UI (AxonHub) and for anyone running a build the launch command does not fit.
#
# A gateway that can be neither attached to nor launched is recorded as
# SKIPPED across its whole column, with the reason. It is never silently
# dropped from the matrix: an absent gateway and a clean gateway must not look
# the same in the results.
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time

MOCK_PORT = 9990  # chosen to avoid 9911/9996/9998/9999 used by the frozen rigs


def port_open(port, host="127.0.0.1", timeout=0.25):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port, deadline_s=25.0):
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if port_open(port):
            return True
        time.sleep(0.25)
    return False


class Gateway:
    """One gateway under test.

    messages_path / chat_path are the two ingresses. `auth` is the header pair
    the gateway's own front door expects (not an upstream credential).
    """

    name = "gateway"
    port = 0
    messages_path = "/v1/messages"
    chat_path = "/v1/chat/completions"
    model = "captured-model"
    auth = {}
    binary = None  # probed with shutil.which; None means attach-only

    def __init__(self, workdir, mock_port=MOCK_PORT):
        self.workdir = workdir
        self.mock_port = mock_port
        self.proc = None
        self.attached = False
        self.skip_reason = None

    # -- lifecycle ------------------------------------------------------

    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def mock_base(self, suffix="/v1"):
        return f"http://127.0.0.1:{self.mock_port}{suffix}"

    def write_config(self):
        """Write a config whose backend points at the mock. Returns a path."""
        raise NotImplementedError

    def launch_argv(self, cfg):
        raise NotImplementedError

    def launch_env(self):
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)  # never hand real keys to the rig
        env.pop("OPENAI_API_KEY", None)
        env["OPENAI_API_KEY"] = "sk-x"
        return env

    def start(self):
        if port_open(self.port):
            self.attached = True
            return True
        if not self.binary or not shutil.which(self.binary.split()[0]):
            self.skip_reason = (
                f"not attached on :{self.port} and `{self.binary or 'launcher'}` "
                "not on PATH"
            )
            return False
        try:
            cfg = self.write_config()
            argv = self.launch_argv(cfg)
        except NotImplementedError:
            self.skip_reason = "no launch recipe; start it yourself and re-run to attach"
            return False
        log = open(os.path.join(self.workdir, f"{self.name}.log"), "ab")
        self.proc = subprocess.Popen(
            argv, cwd=self.workdir, env=self.launch_env(),
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        if not wait_for_port(self.port):
            self.skip_reason = (
                f"launched but :{self.port} never opened; see {self.name}.log"
            )
            self.stop()
            return False
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=8)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        self.proc = None

    # -- request shaping ------------------------------------------------

    def messages_headers(self, extra=None):
        h = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        h.update(self.auth)
        h.update(extra or {})
        return h

    def chat_headers(self, extra=None):
        h = {"content-type": "application/json"}
        h.update(self.auth)
        h.update(extra or {})
        return h

    def shape_body(self, body):
        """Stamp the gateway's own model id onto a probe body."""
        b = json.loads(json.dumps(body))
        b["model"] = self.model
        return b


class LiteLLM(Gateway):
    name = "litellm"
    port = 4008
    binary = "litellm"
    model = "mock"
    auth = {"authorization": "Bearer sk-kairo-sweep"}

    def write_config(self):
        cfg = os.path.join(self.workdir, "litellm-sweep.yaml")
        with open(cfg, "w") as f:
            f.write(
                "model_list:\n"
                "  - model_name: mock\n"
                "    litellm_params:\n"
                "      model: openai/captured-model\n"
                f"      api_base: {self.mock_base()}\n"
                "      api_key: sk-x\n"
            )
        return cfg

    def launch_argv(self, cfg):
        return ["litellm", "--config", cfg, "--port", str(self.port)]


class Switchyard(Gateway):
    name = "switchyard"
    port = 9004
    binary = "switchyard-server"
    model = "captured-model"

    def write_config(self):
        cfg = os.path.join(self.workdir, "switchyard-sweep.toml")
        with open(cfg, "w") as f:
            f.write(
                "schema_version = 1\n"
                "[llm_clients.local]\n"
                'format = "openai_chat"\n'
                f'base_url = "{self.mock_base()}"\n'
                "[llm_clients.local.extra_headers]\n"
                'authorization = "Bearer sk-x"\n'
                "[targets.cap]\n"
                'id = "captured-model"\n'
                'llm_client = "local"\n'
                "[routes.primary]\n"
                'id = "captured-model"\n'
                'type = "passthrough"\n'
                'target = "cap"\n'
            )
        return cfg

    def launch_argv(self, cfg):
        return ["switchyard-server", "--config", cfg, "--port", str(self.port)]


class Bifrost(Gateway):
    name = "bifrost"
    port = 8085
    binary = "npx"
    model = "mockoai/captured-model"

    def write_config(self):
        d = os.path.join(self.workdir, "bifrost-app")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({
                "$schema": "https://www.getbifrost.ai/schema",
                "config_store": {"enabled": False},
                "client": {"enable_logging": True, "initial_pool_size": 8},
                "providers": {
                    "mockoai": {
                        "keys": [{"name": "k1", "value": "sk-x", "weight": 1,
                                  "models": ["*"]}],
                        "network_config": {
                            "base_url": f"http://127.0.0.1:{self.mock_port}"},
                        "custom_provider_config": {"base_provider_type": "openai"},
                    }
                },
            }, f, indent=2)
        return d

    def launch_argv(self, cfg):
        return ["npx", "-y", "@maximhq/bifrost", "-app-dir", cfg,
                "-port", str(self.port)]


class GoModel(Gateway):
    name = "gomodel"
    port = 8081
    binary = "gomodel"
    model = "openai/captured-model"
    auth = {"authorization": "Bearer kairo-sweep"}

    def write_config(self):
        cfg = os.path.join(self.workdir, "config.yaml")
        with open(cfg, "w") as f:
            f.write(
                "server:\n"
                f'  port: "{self.port}"\n'
                '  master_key: "kairo-sweep"\n'
                "  enable_passthrough_routes: true\n"
                "\n"
                "providers:\n"
                "  mock:\n"
                "    type: openai\n"
                f'    base_url: "{self.mock_base()}"\n'
                '    api_key: "sk-x"\n'
                "    models:\n"
                "      - id: captured-model\n"
            )
        return cfg

    def launch_env(self):
        env = super().launch_env()
        env["GOMODEL_MASTER_KEY"] = "kairo-sweep"
        env["OPENAI_BASE_URL"] = self.mock_base()
        return env

    def launch_argv(self, cfg):
        return ["gomodel"]


class AxonHub(Gateway):
    # Channels live in AxonHub's database, configured through its UI or admin
    # API, so there is no config file this rig can write. Attach-only: start
    # AxonHub yourself with an OpenAI channel whose baseURL is the mock, then
    # export AXONHUB_KEY and re-run.
    name = "axonhub"
    port = 8090
    binary = None
    model = "captured-model"

    def __init__(self, workdir, mock_port=MOCK_PORT):
        super().__init__(workdir, mock_port)
        self.auth = {"authorization": f"Bearer {os.environ.get('AXONHUB_KEY', '')}"}

    def start(self):
        ok = super().start()
        if ok and not os.environ.get("AXONHUB_KEY"):
            self.skip_reason = "attached on :8090 but AXONHUB_KEY is unset"
            return False
        return ok


ALL = [LiteLLM, Switchyard, Bifrost, GoModel, AxonHub]


def build(names, workdir, mock_port=MOCK_PORT):
    chosen = [g for g in ALL if not names or g.name in names]
    return [g(workdir, mock_port) for g in chosen]
