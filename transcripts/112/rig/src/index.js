// Finding 112 rig. Two entry points:
//
//   POST /raw    {model, inputs}      -> env.AI.run(model, inputs, {returnRawResponse: true})
//                                       with no gateway and no SDK in between. The
//                                       reply is {status, headers, body}, where body is
//                                       the exact text Workers AI returned.
//   POST /agent  {model, messages}    -> an AI SDK agent loop on createAI from
//                                       agents/models/ai-sdk, with a get_weather tool
//                                       that really executes. Every binding.run call
//                                       the provider makes is recorded with its raw body.

import { createAI } from "agents/models/ai-sdk";
import { generateText, jsonSchema, stepCountIs, tool } from "ai";

function headersObject(headers) {
	const out = {};
	for (const [k, v] of headers) out[k] = v;
	return out;
}

// Records each binding.run call (inputs, options, status, raw body) without
// changing what the provider receives.
function recordingBinding(ai, log) {
	return new Proxy(ai, {
		get(target, prop) {
			if (prop === "run") {
				return async (model, inputs, opts) => {
					const entry = { model, inputs, opts: opts ? { ...opts, signal: undefined } : opts };
					log.push(entry);
					const r = await target.run(model, inputs, opts);
					if (r instanceof Response) {
						entry.status = r.status;
						entry.headers = headersObject(r.headers);
						entry.pending = r.clone().text().then((t) => (entry.body = t));
					}
					return r;
				};
			}
			const v = Reflect.get(target, prop);
			return typeof v === "function" ? v.bind(target) : v;
		},
	});
}

export default {
	async fetch(req, env) {
		const url = new URL(req.url);
		const body = req.method === "POST" ? await req.json() : {};

		if (url.pathname === "/raw") {
			const r = await env.AI.run(body.model, body.inputs, { returnRawResponse: true });
			return Response.json({
				status: r.status,
				headers: headersObject(r.headers),
				body: await r.text(),
			});
		}

		if (url.pathname === "/agent") {
			const log = [];
			const executions = [];
			const ai = createAI({ binding: recordingBinding(env.AI, log) });
			const tools = {
				get_weather: tool({
					description: "Get current weather for a city",
					inputSchema: jsonSchema({
						type: "object",
						properties: { city: { type: "string" } },
						required: ["city"],
					}),
					execute: async ({ city }) => {
						executions.push(city);
						return { city, temp_c: city === "Paris" ? 14 : 22, sky: "clear" };
					},
				}),
			};
			let result;
			try {
				const r = await generateText({
					model: ai(body.model),
					tools,
					stopWhen: stepCountIs(body.maxSteps ?? 5),
					messages: body.messages,
					temperature: 0,
					maxOutputTokens: 400,
					maxRetries: 0,
				});
				result = {
					text: r.text,
					finishReason: r.finishReason,
					steps: r.steps.map((s) => ({
						toolCalls: s.toolCalls.map((c) => ({
							toolCallId: c.toolCallId,
							input: c.input,
							invalid: c.invalid ?? false,
						})),
						toolErrors: s.content
							.filter((c) => c.type === "tool-error")
							.map((c) => String(c.error).slice(0, 300)),
						toolResults: s.toolResults.length,
						finishReason: s.finishReason,
					})),
				};
			} catch (e) {
				result = { error: String(e?.stack ?? e) };
			}
			for (const e of log) if (e.pending) await e.pending.catch(() => {});
			return Response.json({
				result,
				executions,
				calls: log.map(({ pending, ...rest }) => rest),
			});
		}

		return new Response("not found", { status: 404 });
	},
};
