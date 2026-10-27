"""LangChain classic ReAct agent on ChatAnthropic, pointed at a LiteLLM proxy's /v1/messages.
create_react_agent binds stop=["\\nObservation"] by default, which ChatAnthropic sends as
stop_sequences. The one tool appends one JSON line per call to a ledger, so executions
are counted exactly even when the model puts newlines into the tool input.
usage: react_agent.py BASE_URL MODEL_ALIAS OUTDIR"""
import json, os, sys, traceback
from langchain_anthropic import ChatAnthropic
from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool
base, alias, outdir = sys.argv[1:4]; os.makedirs(outdir, exist_ok=True)
ledger = os.path.join(outdir, "ledger.txt"); open(ledger, "w").close()

@tool
def record_order(sku: str) -> str:
    """Record a customer order for the given SKU. Call once per order."""
    with open(ledger, "a") as f: f.write(json.dumps(sku) + "\n")
    return f"order for {sku.strip()} recorded"

PROMPT = PromptTemplate.from_template("""Answer the following questions as best you can. You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin!

Question: {input}
Thought:{agent_scratchpad}""")
llm = ChatAnthropic(model=alias, base_url=base, api_key="sk-test-master", max_tokens=1024, max_retries=0, timeout=120)
agent = AgentExecutor(agent=create_react_agent(llm, [record_order], PROMPT), tools=[record_order],
                      max_iterations=6, handle_parsing_errors=True)
result = {"model_alias": alias}
try:
    out = agent.invoke({"input": "Record one order for SKU A-17, then reply that it is done."})
    result.update(outcome="completed", final_answer=out["output"])
except Exception as e:
    result.update(outcome="raised", exception=f"{type(e).__module__}.{type(e).__name__}", message=str(e)[:400])
    open(os.path.join(outdir, "traceback.txt"), "w").write(traceback.format_exc())
result["tool_executions"] = [json.loads(l) for l in open(ledger).read().splitlines() if l]
json.dump(result, open(os.path.join(outdir, "result.json"), "w"), indent=1)
print(json.dumps(result)[:300])
