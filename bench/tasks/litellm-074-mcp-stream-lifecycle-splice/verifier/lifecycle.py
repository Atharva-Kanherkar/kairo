"""OpenAI Responses streaming invariants (ported from kairo's responses_single_lifecycle)."""

import json


def events(raw: bytes):
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data == "[DONE]":
            continue
        out.append(json.loads(data))
    return out


def lifecycle_problem(raw: bytes):
    """None when the body is exactly one Responses lifecycle, else a description."""
    try:
        evs = events(raw)
    except json.JSONDecodeError as exc:
        return f"unparseable data frame: {exc}"
    if not evs:
        return "no JSON events"
    created = [i for i, e in enumerate(evs) if e.get("type") == "response.created"]
    done = [i for i, e in enumerate(evs) if e.get("type") in ("response.completed", "response.incomplete", "response.failed")]
    if len(created) != 1 or len(done) != 1:
        return f"{len(created)} response.created and {len(done)} terminal events, expected one each"
    if created[0] >= done[0] or done[0] + 1 != len(evs):
        return "the terminal event is not the last JSON event of the only lifecycle"
    # The terminal event may carry a different id than response.created: upstream reports
    # the final round's id there on purpose so that previous_response_id continues from it.
    seen = {}
    for e in evs:
        if e.get("type") != "response.output_item.added":
            continue
        idx, item_id = e.get("output_index"), (e.get("item") or {}).get("id")
        if not isinstance(idx, int) or item_id is None:
            return "response.output_item.added without numeric output_index or item id"
        if seen.setdefault(idx, item_id) != item_id:
            return f"output_index {idx} names two items: {seen[idx]!r} and {item_id!r}"
    return None
