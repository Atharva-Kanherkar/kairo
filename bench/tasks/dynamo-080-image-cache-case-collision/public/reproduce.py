from kairo_verify import show
from rigs import dynamo_images

routes = {"/Cat.png": (255, 0, 0), "/cat.png": (0, 0, 255)}
for cache_size in (8, 0):
    pixels, hits = dynamo_images.load(routes, ["/Cat.png", "/cat.png"], cache_size=cache_size)
    show(f"cache_size={cache_size}", {"requested": ["/Cat.png", "/cat.png"],
                                     "decoded_top_left_pixel": pixels, "origin_hits": hits,
                                     "origin_serves": {k: v for k, v in routes.items()}})
