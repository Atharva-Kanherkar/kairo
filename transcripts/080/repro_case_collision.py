"""Deterministic repro: ImageLoader lowercases the whole URL for its cache key.

Two distinct images are served at case-sensitive paths /Cat.png (red) and
/cat.png (blue). If the cache key were correct, requesting both in sequence
must return red then blue and hit the server twice. Because the key is
normalized_url.lower(), the second request collides with the first and
returns the first image without any network fetch.

The compiled Rust `dynamo.runtime` is the only shimmed module; ImageLoader,
validate_media_url, and the aiohttp fetch path are imported verbatim from the
pinned checkout at 5593e8857.
"""

import asyncio
import io
import os
import sys
import types
from datetime import datetime, timezone

REPO_SRC = "/Users/atharva/Documents/ChatGPT/NVIDIA_DYNAMO_work/components/src"
BINDINGS_SRC = "/Users/atharva/Documents/ChatGPT/NVIDIA_DYNAMO_work/lib/bindings/python/src"
SHIM_DIR = "/var/folders/h7/02529x_j2196_9dnc1k5pnr80000gn/T/opencode/shim"

# --- shim only the compiled Rust runtime via a namespace-package stub -----
sys.path.insert(0, SHIM_DIR)
sys.path.insert(1, REPO_SRC)
sys.path.insert(2, BINDINGS_SRC)

os.environ["DYN_MM_ALLOW_INTERNAL"] = "1"  # documented switch for http:// + private IPs

from aiohttp import web
from PIL import Image

from dynamo.common.http import fetch_bytes
from dynamo.common.http.url_validator import UrlValidationPolicy

# dynamo.common.multimodal.__init__ imports torch-backed cache managers; the
# defect lives in image_loader.py alone, so register the package without
# running its __init__ and import the module verbatim from the checkout.
import dynamo.common

_mm_pkg = types.ModuleType("dynamo.common.multimodal")
_mm_pkg.__path__ = [REPO_SRC + "/dynamo/common/multimodal"]
sys.modules["dynamo.common.multimodal"] = _mm_pkg
from dynamo.common.multimodal.image_loader import ImageLoader


def png_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, "PNG")
    return buf.getvalue()


RED = png_bytes((255, 0, 0))
BLUE = png_bytes((0, 0, 255))
GREEN = png_bytes((0, 255, 0))


class Server:
    def __init__(self):
        self.hits = []
        self.app = web.Application()
        self.app.router.add_get("/{tail:.*}", self._handler)
        self.runner = web.AppRunner(self.app)
        self.site = None

    async def _handler(self, request):
        self.hits.append(request.path)
        if request.path == "/Cat.png":
            return web.Response(body=RED, content_type="image/png")
        if request.path == "/cat.png":
            return web.Response(body=BLUE, content_type="image/png")
        if request.path == "/dog.png":
            return web.Response(body=GREEN, content_type="image/png")
        return web.Response(status=404)

    async def start(self):
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        sock = self.site._server.sockets[0]
        return sock.getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()


async def scenario(cache_size, order):
    """order: list of paths to request in sequence. Returns (pixels, hits)."""
    server = Server()
    port = await server.start()
    try:
        loader = ImageLoader(cache_size=cache_size, url_policy=UrlValidationPolicy.from_env())
        pixels = []
        for path in order:
            img = await loader.load_image(f"http://127.0.0.1:{port}{path}")
            pixels.append(list(img.getpixel((0, 0))))
        return pixels, list(server.hits)
    finally:
        await server.stop()


ORIGIN_COLORS = {"/Cat.png": [255, 0, 0], "/cat.png": [0, 0, 255], "/dog.png": [0, 255, 0]}


def log_line(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


async def main():
    import json

    print("=" * 78)
    print("DYNAMO ImageLoader lowercase cache-key repro (checkout 5593e8857)")
    print("=" * 78)

    bug_records = []
    control_records = []
    distinct_records = []

    # Control A: direct fetch proves the server serves blue at /cat.png
    server = Server()
    port = await server.start()
    try:
        direct = await fetch_bytes(f"http://127.0.0.1:{port}/cat.png", 30.0)
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(direct)) as im:
            direct_px = list(im.convert("RGB").getpixel((0, 0)))
        log_line(f"CONTROL direct fetch /cat.png -> {direct_px} (expect (0,0,255) blue)")
    finally:
        await server.stop()

    # Control B: cache enabled, URLs differ beyond case -> both fetched
    px, hits = await scenario(8, ["/Cat.png", "/dog.png"])
    log_line(f"CONTROL distinct URLs /Cat.png,/dog.png -> pixels={px} hits={hits}")
    distinct_records.append(
        {
            "scenario": "distinct-urls",
            "cache_size": 8,
            "requested": ["/Cat.png", "/dog.png"],
            "origin_hits": hits,
            "decoded": px,
            "origin_colors": ORIGIN_COLORS,
        }
    )

    # Control C: cache disabled (cache_size=0), case-differing URLs
    for i in range(5):
        px, hits = await scenario(0, ["/Cat.png", "/cat.png"])
        log_line(f"CONTROL cache off run {i+1}: pixels={px} hits={hits} (expect red,blue; 2 hits)")
        control_records.append(
            {
                "scenario": "cache-off",
                "cache_size": 0,
                "requested": ["/Cat.png", "/cat.png"],
                "origin_hits": hits,
                "decoded": px,
                "origin_colors": ORIGIN_COLORS,
            }
        )

    # Bug: cache enabled (default), case-differing URLs
    for i in range(5):
        px, hits = await scenario(8, ["/Cat.png", "/cat.png"])
        log_line(f"BUG run {i+1}: pixels={px} hits={hits} (correct would be red,blue; 2 hits)")
        bug_records.append(
            {
                "scenario": "bug",
                "cache_size": 8,
                "requested": ["/Cat.png", "/cat.png"],
                "origin_hits": hits,
                "decoded": px,
                "origin_colors": ORIGIN_COLORS,
            }
        )

    # Ordering: /cat.png first, then /Cat.png -> first writer wins
    px, hits = await scenario(8, ["/cat.png", "/Cat.png", "/cat.png"])
    log_line(f"ORDERING /cat.png,/Cat.png,/cat.png -> pixels={px} hits={hits}")
    bug_records.append(
        {
            "scenario": "ordering",
            "cache_size": 8,
            "requested": ["/cat.png", "/Cat.png", "/cat.png"],
            "origin_hits": hits,
            "decoded": px,
            "origin_colors": ORIGIN_COLORS,
        }
    )

    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    for name, records in (
        ("capture-bug.jsonl", bug_records),
        ("capture-control.jsonl", control_records),
        ("capture-distinct.jsonl", distinct_records),
    ):
        with open(os.path.join(out_dir, name), "w") as f:
            for r in records:
                f.write(json.dumps({"body": r}) + "\n")
        log_line(f"wrote {out_dir}/{name} ({len(records)} records)")


if __name__ == "__main__":
    asyncio.run(main())
