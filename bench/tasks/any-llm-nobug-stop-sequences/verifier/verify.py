"""Hidden verifier (no-bug task): stop_sequences already map to OpenAI `stop`; keep it that way."""

from kairo_verify import Results, run
from rigs import anyllm

R = Results()


def body(out):
    assert out.error is None, f"client raised {out.error!r}"
    assert out.forwarded is not None, "nothing reached the upstream"
    return out.forwarded


multi = anyllm.call(messages=[{"role": "user", "content": "count to ten"}], stop_sequences=["SEVEN", "\n\nUser:"])
single = anyllm.call(messages=[{"role": "user", "content": "write a haiku"}], stop_sequences=["###"])
none = anyllm.call(messages=[{"role": "user", "content": "hi"}])
plain = anyllm.call(messages=[{"role": "user", "content": "hello"}], system="be brief")

with R.test("stop_sequences_forwarded_as_stop"):
    assert body(multi).get("stop") == ["SEVEN", "\n\nUser:"], body(multi).get("stop")

with R.test("single_stop_sequence_forwarded"):
    stop = body(single).get("stop")
    assert stop in (["###"], "###"), stop

with R.test("no_stop_invented"):
    assert "stop" not in body(none) or body(none)["stop"] in (None, []), body(none).get("stop")

with R.test("plain_conversation_unchanged"):
    msgs = body(plain)["messages"]
    assert msgs[0] == {"role": "system", "content": "be brief"}, msgs
    assert msgs[-1] == {"role": "user", "content": "hello"}, msgs
    assert plain.response.content[0].text == "ok"

code, out = run("python -m pytest -q -p no:cacheprovider tests/unit/test_messages_compat.py", cwd="/work/repo", timeout=600)
R.check("upstream_unit_tests_messages_compat", code == 0, out)
R.write()
