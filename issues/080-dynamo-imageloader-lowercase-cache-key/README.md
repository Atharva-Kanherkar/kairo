# 080, Dynamo ImageLoader lowercases the whole URL for its cache key, so a case-differing image URL decodes the wrong image with no error

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo), no matching ticket. The identical flaw was flagged by CodeRabbit on the PR that introduced the cache ([#3634](https://github.com/ai-dynamo/dynamo/pull/3634), review comment 2464485829, 2025-10-27, severity Major: "Cache key lowercasing can return the wrong image"), received zero replies, and the code merged anyway. Classification: `discussed-no-ticket` at time of writing; now reported upstream as [issue #15100](https://github.com/ai-dynamo/dynamo/issues/15100) with fix [PR #15101](https://github.com/ai-dynamo/dynamo/pull/15101) (filed 2026-09-19). Search terms used: `image cache`, `lowercase`, `case-sensitive`, `wrong image`, `image cache collision`, `cache key`; issues #9063, #12309, #8359, #10119 checked and unrelated. Date checked 2026-09-19.
- **Tool under test**: NVIDIA Dynamo, commit `5593e8857` (`feat(runtime): add per-request lifecycle instrumentation (#14101)`), equal to upstream `main` on 2026-09-19. Defect at `components/src/dynamo/common/multimodal/image_loader.py:221`, reached by the vLLM path (`components/src/dynamo/vllm/multimodal_handlers/encode_worker_handler.py:388`) and the SGLang path (`request_handlers/multimodal/encode_worker_handler.py:280`, `request_handlers/llm/decode_handler.py:267`).
- **Reproduced**: 2026-09-19, macOS arm64, no GPU, Python 3.12 venv (Pillow, aiohttp, pydantic), local case-sensitive aiohttp origin, `DYN_MM_ALLOW_INTERNAL=1`. **6/6 bug runs and 6/6 control runs, zero variance.**

## What breaks

Two image URLs that differ only in letter case are distinct resources: RFC 3986 paths and queries are case-sensitive, and Dynamo's own fetcher requests the URL with its original case. But `ImageLoader` builds its LRU cache key as `normalized_url.lower()`. The second request hits the cache, never reaches the origin, and silently decodes the first image.

A user sends two chat requests to a Dynamo-fronted vision model, one with `.../Cat.png`, one with `.../cat.png`. The second answer describes the first image. No error is raised, and the origin log shows only one fetch. On a shared serving instance, distinct requests can observe each other's image bytes through the cache, which crosses the request boundary the cache is supposed to respect.

Who it hurts: anyone serving multimodal models behind Dynamo from case-sensitive storage (S3 static hosting is case-sensitive), and anyone whose pipeline emits URLs that differ only by case (CDN rewrites, case-preserving proxies, generated signed or canonical paths). The cache is on by default (`DYN_MM_IMAGE_CACHE_SIZE` defaults to 8; zero disables).

## Wire evidence

`transcripts/080/`:

- `capture-bug.jsonl`, 6 records (5 identical bug runs plus one ordering run). Each record lists the requested paths, the origin's observed hits, and the decoded pixel per request. Every bug record: requested `["/Cat.png","/cat.png"]`, origin hits `["/Cat.png"]`, decoded `[[255,0,0],[255,0,0]]`. The ordering run (`/cat.png` first) shows first-writer-wins: hits `["/cat.png"]`, decoded `[[0,0,255],[0,0,255],[0,0,255]]`.
- `capture-control.jsonl`, 5 records, cache disabled (`cache_size=0`), same URL pair: hits `["/Cat.png","/cat.png"]`, decoded `[[255,0,0],[0,0,255]]`, all 5/5.
- `capture-distinct.jsonl`, 1 record, cache enabled, URLs differing beyond case: both fetched, both correct. The cache itself works; only the key conflates case.
- `repro_case_collision.py`, the harness: a local aiohttp origin serving red at `/Cat.png`, blue at `/cat.png`, green at `/dog.png`, with exact-path dispatch, plus the verbatim `ImageLoader`, `validate_media_url`, and aiohttp `fetch_bytes` imported from the pinned checkout. Only the compiled Rust modules (`dynamo.runtime`, `dynamo._core`, `dynamo.llm`) are shimmed, and the exercised path never touches them.
- `repro-run-2026-09-19.txt`, the full run output, including the direct-fetch control proving the origin serves blue at `/cat.png`.

## Root cause

`components/src/dynamo/common/multimodal/image_loader.py:221`:

```python
if parsed_url.scheme in ("http", "https"):
    key = normalized_url.lower()
```

`validate_media_url` returns the URL unchanged (case preserved), so the key collapses case-sensitive path and query components. The lowercase key arrived with the cache (originally `image_url.lower()`) and was kept through #8158 and #8282 (`normalized_url.lower()`); it is still on current `main`.

## Test

- `image_url_cache_key_case_sensitive` in `crates/harness/src/checks.rs`. The invariant, not the bug: for every loader session that requested a case-colliding URL pair, the origin must receive one hit per requested URL and each decoded image must match the resource the origin serves for that URL.
- Conformance: `dynamo_image_cache_case_collision_violation` asserts `Violation` on `transcripts/080/capture-bug.jsonl`; `dynamo_image_cache_cache_off_control_is_conformant` and `dynamo_image_cache_distinct_urls_control_is_conformant` assert `Conformant` on the controls. The day Dynamo stops conflating case, the violation test flips and tells us.
- Unit tests for the checker: `image_cache_case_collision_is_caught`, `image_cache_case_distinct_urls_is_conformant`, `image_cache_no_case_pair_skips`.
