# Plain-text documents sent to `/v1/messages` never reach OpenAI models

We send reports to the model as Anthropic `document` blocks with a plain-text
source, the documented way to attach a text document:

```json
{"role": "user", "content": [
  {"type": "text", "text": "Summarize the attached incident report."},
  {"type": "document", "source": {"type": "text", "media_type": "text/plain",
                                  "data": "At 09:12 UTC the primary database failed over..."}}
]}
```

Against an OpenAI deployment behind the LiteLLM proxy, the model replies that
there is no document. The request LiteLLM sends to OpenAI contains only the
"Summarize the attached incident report." text; the document is gone, with
HTTP 200 and no warning. PDF documents sent as base64 in the same position do
arrive. We expected plain-text documents to reach the model too, in whatever
form the OpenAI side accepts.
