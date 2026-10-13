# Multimodal requests with image URLs that differ only in case get the wrong image

We serve a vision model behind Dynamo. Images come from an S3 static-website
bucket, which is case-sensitive. Two chat requests arrive a few seconds apart:
one references `https://images.example.com/products/Cat.png` and the other
`https://images.example.com/products/cat.png`. They are different files.

The second request's answer describes the first image. There is no error, and
the image origin's access log shows only one fetch: the second image was never
requested. The image cache is enabled with its default size
(`DYN_MM_IMAGE_CACHE_SIZE`), and when we set it to `0` both requests get the
right image and the origin sees two fetches.

Distinct images must never be served for each other. The loader fetches the URL
with its original case, so whatever the cache does has to agree with that.
