#!/usr/bin/env python3
"""LangChain ChatOpenAI through the agentgateway rig.

langchain-openai switches to openai-python's accumulating helper,
`beta.chat.completions.stream()`, whenever `response_format` is set. Without it,
LangChain iterates raw chunks and is unaffected.

Usage: langchain_consumer.py PORT fmt|plain
"""
import sys
import traceback

from langchain_openai import ChatOpenAI

port = int(sys.argv[1])
fmt = sys.argv[2] == "fmt"
tool = {"type": "function", "function": {"name": "get_weather", "description": "w",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}
llm = ChatOpenAI(model="captured-model", base_url=f"http://127.0.0.1:{port}/v1",
                 api_key="k", streaming=True, max_retries=0)
kw = {"response_format": {"type": "json_object"}} if fmt else {}
model = llm.bind(tools=[tool], **kw)
try:
    agg = None
    for ch in model.stream("weather in sf?"):
        agg = ch if agg is None else agg + ch
    print(f"port {port} response_format={fmt}: OK tool_calls={agg.tool_calls}")
except BaseException as e:
    f = traceback.extract_tb(e.__traceback__)[-1]
    print(f"port {port} response_format={fmt}: RAISED {type(e).__name__} "
          f"at {f.filename.split('site-packages/')[-1]}:{f.lineno}")
