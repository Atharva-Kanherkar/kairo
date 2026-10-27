# Streamed tool-call arguments come out mangled through Switchyard

Our coding agent streams from Switchyard's `/v1/messages` with an OpenAI
backend. Tool calls with large arguments (file patches with newlines, quotes,
and non-ASCII text) sometimes fail to parse or apply, and we suspect
Switchyard mishandles OpenAI streams that split one tool call's `arguments`
across many deltas when it converts them into Anthropic `input_json_delta`
events. Please fix the reassembly so the agent receives exactly the arguments
the model produced.
