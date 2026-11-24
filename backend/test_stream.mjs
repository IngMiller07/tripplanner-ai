process.env.ORCHESTRATOR_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tripplannera2a_OrchestratorAgent-AbC123xyz9";
process.env.AWS_ACCESS_KEY_ID = "AKID"; process.env.AWS_SECRET_ACCESS_KEY = "secret"; process.env.AWS_SESSION_TOKEN = "tok";
let meta = null, pending = null, chunks = [];
// Modelo del comportamiento REAL de Lambda (RESPONSE_STREAM): el estado y las cabeceras solo se aplican si se escribe
// algo antes de end(); si no, Lambda responde 200 application/octet-stream sin cabeceras (esto rompia el preflight CORS).
globalThis.awslambda = {
  streamifyResponse: (f) => f,
  HttpResponseStream: { from: (s, m) => { pending = m; return s; } },
};
const mkStream = () => ({
  write: (c) => { if (pending) { meta = pending; pending = null; } chunks.push(typeof c === "string" ? c : Buffer.from(c).toString()); },
  end: () => { if (pending) { meta = { statusCode: 200, headers: { "Content-Type": "application/octet-stream" } }; pending = null; } chunks.push("<END>"); },
});
let seen = null;
globalThis.fetch = async (url, opts) => {
  seen = { url, opts };
  const enc = new TextEncoder();
  return new Response(new ReadableStream({ start(c) { c.enqueue(enc.encode('data: {"type":"text","data":"Hola"}\n\n')); c.enqueue(enc.encode('data: {"type":"done"}\n\n')); c.close(); } }), { status: 200 });
};
const { handler, buildPayload } = await import("./lambdas/stream_chat/index.mjs");
const run = async (ev) => { meta = null; pending = null; chunks = []; await handler(ev, mkStream()); return { meta, out: chunks.join("") }; };

let r = await run({ requestContext: { http: { method: "OPTIONS" } } });
console.log("OPTIONS ->", r.meta.statusCode, r.meta.headers["Access-Control-Allow-Origin"], r.meta.headers["Access-Control-Allow-Headers"]);
if (r.meta.statusCode !== 200 || r.meta.headers["Access-Control-Allow-Origin"] !== "*" || !/content-type/i.test(r.meta.headers["Access-Control-Allow-Headers"] || "")) {
  console.log("FALLO: el preflight CORS no devuelve las cabeceras"); process.exit(1);
}
r = await run({ requestContext: { http: { method: "POST" } }, body: JSON.stringify({ prompt: "", userId: "x" }) });
console.log("prompt vacio ->", r.meta.statusCode, r.out.trim());
r = await run({ requestContext: { http: { method: "POST" } }, body: JSON.stringify({ prompt: "x".repeat(2001) }) });
console.log("prompt largo ->", r.meta.statusCode);
r = await run({ requestContext: { http: { method: "POST" } }, body: JSON.stringify({ prompt: "Quiero ir a Cartagena", userId: "miller", sessionId: "abc" }) });
console.log("OK ->", r.meta.statusCode, r.meta.headers["Content-Type"]);
console.log(r.out);
console.log("URL:", seen.url);
console.log("headers enviados:", Object.keys(seen.opts.headers).join(","));
console.log("session id largo >=33:", seen.opts.headers["x-amzn-bedrock-agentcore-runtime-session-id"].length >= 33, "| host NO enviado:", !("host" in seen.opts.headers));
console.log("body:", seen.opts.body);
globalThis.fetch = async () => new Response("AccessDenied", { status: 403 });
r = await run({ requestContext: { http: { method: "POST" } }, body: JSON.stringify({ prompt: "hola" }) });
console.log("403 upstream ->", r.out.replace(/\n/g, " "));
globalThis.fetch = async () => { throw new Error("ECONNRESET"); };
r = await run({ requestContext: { http: { method: "POST" } }, body: JSON.stringify({ prompt: "hola" }) });
console.log("red caida ->", r.out.replace(/\n/g, " "));
