#!/usr/bin/env python3
"""Sweep integer magnitudes through the pinned coordinator to locate the rounding boundary."""
import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reproduce import PIN, Capture, CaptureServer, exchange, response_body, request  # noqa: E402

VALUES = [
    42,
    9007199254740992,
    9007199254740993,
    9007199254740994,
    9007199254740995,
    18014398509481984,
    18014398509481985,
    9223372036854775807,
    18446744073709551615,
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('/tmp/kairo-097-boundary'))
    args = parser.parse_args()
    args.target = args.target.resolve()
    args.binary = args.binary.resolve()
    args.output = args.output.resolve()
    output = args.output
    actual = subprocess.check_output(['git', '-C', str(args.target), 'rev-parse', 'HEAD'], text=True).strip()
    assert actual == PIN, (actual, PIN)
    import shutil
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    with CaptureServer(('127.0.0.1', 0), Capture) as upstream:
        upstream.output = output
        backend_port = upstream.server_address[1]
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            coordinator_port = probe.getsockname()[1]
        config = f'''server:
  listen_addr: "127.0.0.1:{coordinator_port}"
  metrics_port: -1
  secure_serving: true
gateway:
  address: "http://127.0.0.1:{backend_port}"
pipeline:
  kv_connector: kv-shared-storage
  ec_connector: ec-shared-storage
  use_openai_format: true
  steps:
    - type: replace-media-urls
    - type: render
      params:
        address: "http://127.0.0.1:{backend_port}"
    - type: encode
    - type: prefill
    - type: decode
'''
        config_path = output / 'coordinator.yaml'
        config_path.write_text(config)
        rows = []
        with (output / 'coordinator-log.txt').open('wb') as log:
            process = subprocess.Popen([str(args.binary), '--config', str(config_path)], cwd=args.target, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    try:
                        _, health = exchange(coordinator_port, '/healthz', b'', 'health', tls=True, method='GET')
                        if health.startswith(b'HTTP/1.1 200'):
                            break
                    except (OSError, Exception):
                        pass
                    time.sleep(.1)
                for index, value in enumerate(VALUES):
                    request_id = f'b-{index:02}'
                    body = json.dumps(request(value), separators=(',', ':')).encode()
                    raw_request, _ = exchange(coordinator_port, '/v1/chat/completions', body, request_id, tls=True)
                    cell = output / request_id
                    forwarded = {}
                    for phase in ('render', 'prefill', 'decode'):
                        stage = json.loads(response_body((cell / f'{phase}-request.http').read_bytes()))
                        forwarded[phase] = stage['response_format']['json_schema']['schema']['properties']['record_id']['enum'][0]
                    rows.append({'submitted': value, 'submitted_text': repr(value), 'forwarded': forwarded, 'identifier': request_id})
                    print(json.dumps(rows[-1]))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                upstream.shutdown()
    with (output / 'boundary.jsonl').open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row) + '\n')
    summary = [{'submitted': r['submitted'], 'render': r['forwarded']['render'], 'equal': r['submitted'] == r['forwarded']['render']} for r in rows]
    for row in summary:
        print(f"{row['submitted']:>22} -> {row['render']:>22} {'preserved' if row['equal'] else 'ROUNDED'}")


if __name__ == '__main__':
    main()
