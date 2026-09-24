"""Drive Dynamo's ImageLoader against a local, case-sensitive image origin.

The origin serves a distinct solid-color PNG per exact path (and query), and
records every request it receives. ``load(paths, cache_size)`` requests each
path through one ImageLoader instance, in order, and returns the decoded
top-left pixel per request plus the origin's hit list.
"""

from __future__ import annotations

import asyncio
import io
import os
from typing import Optional

os.environ.setdefault("DYN_MM_ALLOW_INTERNAL", "1")  # documented switch for http:// and loopback origins

from aiohttp import web  # noqa: E402
from PIL import Image  # noqa: E402


def png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, "PNG")
    return buf.getvalue()


class Origin:
    def __init__(self, routes: dict[str, tuple[int, int, int]]) -> None:
        self.routes = {k: png(v) for k, v in routes.items()}
        self.hits: list[str] = []
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._handle)
        self.runner = web.AppRunner(app)
        self.port = 0

    async def _handle(self, request: web.Request) -> web.Response:
        target = request.path_qs
        self.hits.append(target)
        body = self.routes.get(target)
        if body is None:
            return web.Response(status=404)
        return web.Response(body=body, content_type="image/png")

    async def __aenter__(self) -> "Origin":
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        await self.runner.cleanup()


async def _load(routes, targets, cache_size: int, host: Optional[str]):
    from dynamo.common.http.url_validator import UrlValidationPolicy
    from dynamo.common.multimodal.image_loader import ImageLoader

    from dynamo.common.http import close_http_client

    async with Origin(routes) as origin:
        loader = ImageLoader(cache_size=cache_size, url_policy=UrlValidationPolicy.from_env())
        pixels = []
        try:
            for target in targets:
                img = await loader.load_image(f"http://{host or '127.0.0.1'}:{origin.port}{target}")
                pixels.append(tuple(img.convert("RGB").getpixel((0, 0))))
        finally:
            # The shared HTTP client binds to one event loop; each load() runs its own loop.
            await close_http_client()
        return pixels, list(origin.hits)


def load(routes: dict[str, tuple[int, int, int]], targets: list[str], cache_size: int = 8,
         host: Optional[str] = None):
    """Returns (decoded pixel per target, origin hits)."""
    return asyncio.run(_load(routes, targets, cache_size, host))
