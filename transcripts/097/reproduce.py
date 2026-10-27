#!/usr/bin/env python3
"""Exercise the pinned coordinator over TLS and record exact synthetic HTTP bytes."""
import argparse
import importlib.metadata
import json
import socket
import socketserver
import ssl
import subprocess
import threading
import time
from pathlib import Path
from jsonschema import Draft202012Validator

PIN = '567e35d752c841521fd7c450e8335e5dae108a7c'
LARGE = 9007199254740993

def schema(identifier):
    return {'type': 'object', 'properties': {'record_id': {'type': 'integer', 'enum': [identifier]}}, 'required': ['record_id'], 'additionalProperties': False}

def request(identifier):
    return {'model': 'capture-model', 'messages': [{'role': 'user', 'content': 'Return the allowed record ID.'}], 'max_tokens': 32, 'response_format': {'type': 'json_schema', 'json_schema': {'name': 'record', 'strict': True, 'schema': schema(identifier)}}}

def exchange(port, path, body, request_id, tls=False, method='POST'):
    raw = (f'{method} {path} HTTP/1.1\r\nHost: localhost:{port}\r\nContent-Type: application/json\r\nX-Request-Id: {request_id}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode() + body
    conn = socket.create_connection(('127.0.0.1', port), timeout=10)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # loopback self-signed testing certificate
        conn = context.wrap_socket(conn, server_hostname='localhost')
    conn.sendall(raw)
    chunks = []
    while data := conn.recv(65536):
        chunks.append(data)
    conn.close()
    return raw, b''.join(chunks)

def response_body(raw):
    head, body = raw.split(b'\r\n\r\n', 1)
    if b'transfer-encoding: chunked' in head.lower():
        result = bytearray()
        while True:
            length, body = body.split(b'\r\n', 1)
            size = int(length.split(b';')[0], 16)
            if size == 0:
                return bytes(result)
            result.extend(body[:size])
            body = body[size + 2:]
    return body

class Capture(socketserver.StreamRequestHandler):
    def handle(self):
        head = bytearray()
        while line := self.rfile.readline():
            head.extend(line)
            if line == b'\r\n':
                break
        lines = bytes(head).split(b'\r\n')
        headers = {k.lower(): v.strip() for line in lines[1:] if b':' in line for k, v in [line.split(b':', 1)]}
        body = self.rfile.read(int(headers.get(b'content-length', b'0')))
        if not lines[0]:
            return
        parsed = json.loads(body)
        identifier = headers.get(b'x-request-id', b'unknown').decode()
        path = lines[0].split()[1].decode()
        phase = headers.get(b'x-llm-d-epp-profile', b'direct').decode()
        if path.endswith('/render'):
            phase = 'render'
            payload = {'token_ids': [1, 2, 3], 'features': {}}
        elif phase == 'prefill':
            payload = {}
        else:
            submitted_schema = parsed['response_format']['json_schema']['schema']
            allowed = submitted_schema['properties']['record_id']['enum'][0]
            instance = {'record_id': allowed}
            Draft202012Validator(submitted_schema).validate(instance)
            payload = {'id': 'chatcmpl-capture', 'object': 'chat.completion', 'created': 0, 'model': 'capture-model', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(instance)}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4}}
        encoded = json.dumps(payload, separators=(',', ':')).encode()
        raw_response = f'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n'.encode() + encoded
        cell = self.server.output / identifier
        cell.mkdir(parents=True, exist_ok=True)
        (cell / f'{phase}-request.http').write_bytes(bytes(head) + body)
        (cell / f'{phase}-response.http').write_bytes(raw_response)
        self.wfile.write(raw_response)

class CaptureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=5)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.target = args.target.resolve()
    args.binary = args.binary.resolve()
    actual = subprocess.check_output(['git', '-C', str(args.target), 'rev-parse', 'HEAD'], text=True).strip()
    assert actual == PIN, (actual, PIN)
    assert not subprocess.check_output(['git', '-C', str(args.target), 'status', '--porcelain'], text=True).strip(), 'target checkout must be clean'
    args.output.mkdir(parents=True, exist_ok=True)
    with CaptureServer(('127.0.0.1', 0), Capture) as upstream:
        upstream.output = args.output
        backend_port = upstream.server_address[1]
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
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
        config_path = args.output / 'coordinator.yaml'
        config_path.write_text(config)
        results = []
        with (args.output / 'coordinator-log.txt').open('wb') as log:
            process = subprocess.Popen([str(args.binary), '--config', str(config_path)], cwd=args.target, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError(f'coordinator exited {process.returncode}; inspect {log.name}')
                    try:
                        _, health = exchange(coordinator_port, '/healthz', b'', 'health', tls=True, method='GET')
                        if health.startswith(b'HTTP/1.1 200'):
                            break
                    except (OSError, ssl.SSLError):
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('health timeout')
                for name, value, direct in [('large', LARGE, False), ('safe', 42, False), ('direct', LARGE, True)]:
                    for run in range(1, args.runs + 1):
                        request_id = f'{name}-{run:02}'
                        original = request(value)
                        raw_request, raw_response = exchange(backend_port if direct else coordinator_port, '/v1/chat/completions', json.dumps(original, separators=(',', ':')).encode(), request_id, tls=not direct)
                        cell = args.output / request_id
                        cell.mkdir(exist_ok=True)
                        (cell / 'client-request.http').write_bytes(raw_request)
                        (cell / 'client-response.http').write_bytes(raw_response)
                        assert raw_response.startswith(b'HTTP/1.1 200'), raw_response
                        reply = json.loads(response_body(raw_response))
                        instance = json.loads(reply['choices'][0]['message']['content'])
                        errors = [e.message for e in Draft202012Validator(schema(value)).iter_errors(instance)]
                        forwarded = {}
                        for phase in (['direct'] if direct else ['render', 'prefill', 'decode']):
                            wire = (cell / f'{phase}-request.http').read_bytes()
                            stage_body = json.loads(response_body(wire))
                            forwarded[phase] = stage_body['response_format']['json_schema']['schema']['properties']['record_id']['enum'][0]
                        result = {'case': name, 'run': run, 'submitted_id': value, 'forwarded_ids': forwarded, 'returned_id': instance['record_id'], 'consumer_validation_errors': errors, 'status': 200}
                        (cell / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
                        results.append(result)
                assert all(r['forwarded_ids'] == {'render': LARGE-1, 'prefill': LARGE-1, 'decode': LARGE-1} and r['consumer_validation_errors'] for r in results if r['case'] == 'large')
                assert all(not r['consumer_validation_errors'] and all(v == r['submitted_id'] for v in r['forwarded_ids'].values()) for r in results if r['case'] != 'large')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                upstream.shutdown()
    summary = {'pin': PIN, 'jsonschema_version': importlib.metadata.version('jsonschema'), 'runs_per_case': args.runs, 'results': results, 'scope': 'Real coordinator forwarding and real JSON Schema consumer validation; deterministic capture upstream, no live inference engine.'}
    (args.output / 'results.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'large_schema_corrupted': sum(r['case'] == 'large' and bool(r['consumer_validation_errors']) for r in results), 'safe_controls': sum(r['case'] == 'safe' and not r['consumer_validation_errors'] for r in results), 'direct_controls': sum(r['case'] == 'direct' and not r['consumer_validation_errors'] for r in results), 'runs_per_case': args.runs}))

if __name__ == '__main__':
    main()
