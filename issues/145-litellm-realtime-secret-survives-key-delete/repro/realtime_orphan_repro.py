"""Kairo 145 repro: a realtime client secret executes model calls after its
authorizing key is deleted. Run with the proxy live and
LITELLM_MASTER_KEY / LITELLM_DATABASE_URL set (names only)."""
import httpx, os, sys

BASE = os.environ.get("KAIRO145_PROXY", "http://127.0.0.1:4100")
M = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}"}, timeout=20)
ok = 0
for i in range(5):
    r = M.post("/key/generate", json={"key_alias": f"rt-orphan-{i}", "user_id": "u-repro", "models": ["mock-model"]})
    k = r.json()["key"]
    C = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {k}"}, timeout=15)
    cs = C.post("/realtime/client_secrets", json={"model": "mock-model"})
    s = cs.json().get("value")
    exp = cs.json().get("expires_at")
    M.post("/key/delete", json={"keys": [k]})
    r2 = httpx.post(f"{BASE}/realtime/calls", headers={"Authorization": f"Bearer {s}"}, json={"model": "mock-model", "messages": [{"role": "user", "content": "ORPHANED-CANARY"}]}, timeout=15)
    print(f"run {i}: expires_at={exp} orphaned call -> {r2.status_code}")
    ok += 1 if r2.status_code == 200 else 0
print("OVERALL:", "PASS - 5/5 orphaned secrets execute" if ok == 5 else "FAIL")
sys.exit(0 if ok == 5 else 1)
