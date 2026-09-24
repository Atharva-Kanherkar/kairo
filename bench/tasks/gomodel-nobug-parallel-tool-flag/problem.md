# Parallel tool calls keep happening through GoModel despite `disable_parallel_tool_use`

Our Anthropic-SDK agent sends
`"tool_choice": {"type": "auto", "disable_parallel_tool_use": true}` to
GoModel's `/v1/messages` (OpenAI provider behind it), because its tools must
run one at a time. We still see turns with several tool calls. Other gateways
are known to drop this flag when they translate Anthropic requests to OpenAI,
and we assume GoModel does too. Please make sure OpenAI receives the
equivalent setting.
