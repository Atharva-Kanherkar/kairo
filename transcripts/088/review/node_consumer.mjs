// openai-node's accumulating helper against the isolate server's variants.
// Usage: node node_consumer.mjs PORT
import OpenAI from "openai";
const port = process.argv[2];
const c = new OpenAI({ baseURL: `http://127.0.0.1:${port}/v1`, apiKey: "k" });
for (const v of ["agw_verbatim", "type_omitted"]) {
  await fetch(`http://127.0.0.1:${port}/__variant`, { method: "POST", body: JSON.stringify({ variant: v }) });
  try {
    const s = c.chat.completions.stream({ model: "m", messages: [{ role: "user", content: "hi" }],
      tools: [{ type: "function", function: { name: "get_weather", parameters: { type: "object", properties: { city: { type: "string" } } } } }] });
    const f = await s.finalChatCompletion();
    const t = f.choices[0].message.tool_calls[0];
    console.log(`${v.padEnd(14)} OK type=${t.type} args=${t.function.arguments}`);
  } catch (e) { console.log(`${v.padEnd(14)} RAISED ${e.constructor.name} ${e.message.slice(0, 120)}`); }
}
