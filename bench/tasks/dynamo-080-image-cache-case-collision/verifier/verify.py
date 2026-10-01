"""Hidden verifier: the ImageLoader cache must never serve one image for a different URL."""

from kairo_verify import Results, run
from rigs import dynamo_images

R = Results()
RED, BLUE, GREEN, WHITE = (255, 0, 0), (0, 0, 255), (0, 255, 0), (255, 255, 255)

with R.test("case_differing_paths_decode_distinct_images"):
    routes = {"/Assets/Logo.PNG": RED, "/assets/logo.png": BLUE}
    pixels, hits = dynamo_images.load(routes, ["/Assets/Logo.PNG", "/assets/logo.png", "/Assets/Logo.PNG"])
    assert pixels == [RED, BLUE, RED], f"decoded {pixels}, origin saw {hits}"

with R.test("case_differing_queries_decode_distinct_images"):
    routes = {"/img?id=AbC": GREEN, "/img?id=abc": WHITE}
    pixels, hits = dynamo_images.load(routes, ["/img?id=AbC", "/img?id=abc"])
    assert pixels == [GREEN, WHITE], f"decoded {pixels}, origin saw {hits}"

with R.test("repeated_url_served_from_cache"):
    routes = {"/Cat.png": RED}
    pixels, hits = dynamo_images.load(routes, ["/Cat.png", "/Cat.png", "/Cat.png"])
    assert pixels == [RED, RED, RED], pixels
    assert hits == ["/Cat.png"], f"cache no longer reuses an identical URL: {hits}"

with R.test("distinct_urls_decode_distinct_images"):
    routes = {"/cat.png": BLUE, "/dog.png": GREEN}
    pixels, hits = dynamo_images.load(routes, ["/cat.png", "/dog.png", "/cat.png"])
    assert pixels == [BLUE, GREEN, BLUE], pixels
    assert hits == ["/cat.png", "/dog.png"], hits

with R.test("cache_disabled_fetches_every_request"):
    routes = {"/Cat.png": RED, "/cat.png": BLUE}
    pixels, hits = dynamo_images.load(routes, ["/Cat.png", "/cat.png", "/Cat.png"], cache_size=0)
    assert pixels == [RED, BLUE, RED], pixels
    assert hits == ["/Cat.png", "/cat.png", "/Cat.png"], hits

code, out = run("python -m pytest -q -p no:cacheprovider components/src/dynamo/common/tests/multimodal/test_image_loader.py",
                cwd="/work/repo", timeout=600)
R.check("upstream_unit_tests_image_loader", code == 0, out)
R.write()
