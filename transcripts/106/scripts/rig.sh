#!/bin/bash
# rig.sh start <migration-limit> | worker <w> | ensure
# env: MODEL TP RPR GMU MML
. ${VENV:-/work/venv}/bin/activate
export DYN_FILE_KV=/tmp/dynkv HF_HOME=/work/hf; [ -z "$VENV" ] && export PYTHONPATH=/work/dynamo/components/src
MODEL=${MODEL:-Qwen/Qwen3-4B-Instruct-2507}
start_worker() {
  w=$1
  TPA=""; [ -n "$TP" ] && TPA="--dyn-tool-call-parser $TP"
  RPA=""; [ -n "$RPR" ] && RPA="--dyn-reasoning-parser $RPR"
  DYN_SYSTEM_PORT=$((9100+w)) setsid nohup python -m dynamo.vllm --model $MODEL --discovery-backend file --event-plane zmq --no-kv-events --gpu-memory-utilization ${GMU:-0.42} --max-model-len ${MML:-8192} $TPA $RPA >> /work/logs/worker$w.log 2>&1 &
  echo $! > /work/worker$w.pid
  for i in $(seq 1 150); do curl -s localhost:$((9100+w))/health | grep -q "\"generate\":\"ready\"" && break; sleep 2; done
  echo "worker$w pid $(cat /work/worker$w.pid) health $(curl -s -o /dev/null -w %{http_code} localhost:$((9100+w))/health)"
}
case $1 in
start)
  pkill -f "m dynam[o].frontend"; pkill -f "m dynam[o].vllm"; sleep 4; rm -rf /tmp/dynkv; mkdir -p /work/logs
  echo "== start $(date -u +%FT%TZ) ML=$2 TP=$TP RPR=$RPR MODEL=$MODEL" | tee -a /work/logs/frontend.log /work/logs/worker1.log /work/logs/worker2.log >/dev/null
  DYN_LOG=${DYN_LOG:-info,dynamo_runtime::pipeline::network::egress=debug,dynamo_llm::migration=debug} setsid nohup python -m dynamo.frontend --http-port 8000 --discovery-backend file --event-plane zmq --migration-limit $2 --enable-anthropic-api --router-mode round-robin ${FEXTRA} >> /work/logs/frontend.log 2>&1 &
  echo $! > /work/frontend.pid
  start_worker 1; start_worker 2
  for i in $(seq 1 60); do curl -s localhost:8000/v1/models | grep -q "\"id\"" && break; sleep 2; done; sleep 5; echo ready;;
worker) start_worker $2;;
ensure) for w in 1 2; do [ "$(curl -s -o /dev/null -w %{http_code} --max-time 3 localhost:$((9100+w))/health)" = 200 ] || start_worker $w; done; sleep 6;;
esac
