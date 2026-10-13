# Agent mode hides one of two executed calls to the same tool

We use Bifrost's MCP agent mode with `tools_to_auto_execute: ["charge", "credit"]`
and a manual `review` tool. In one turn the model asks for
`charge(invoice=alpha)`, `charge(invoice=beta)`, and `review`. Bifrost runs both
`charge` calls against the MCP server (we see two executions) and returns the
turn to us with the pending `review` call and a summary of what already ran.

The summary contains only one `charge` result. Our application reads that
summary to learn which side effects happened before it handles the pending
call, so it concludes that the second charge never ran and charges again.
When the two calls go to different tools (`charge` and `credit`), both results
are reported. Repeated calls to one tool in a single turn are normal for us
(two invoices, two files), and every executed call has its own tool-call id.

Relevant config:

```json
"mcp": {"client_configs": [{"name": "Billing", "connection_type": "http",
  "connection_string": "http://127.0.0.1:9000/mcp",
  "tools_to_execute": ["*"], "tools_to_auto_execute": ["charge", "credit"]}]}
```
