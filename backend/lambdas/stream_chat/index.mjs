// TripPlanner-StreamChat  (Canal 2: streaming HTTP hacia AgentCore, SIN API Gateway)
//
// Lambda Function URL en modo RESPONSE_STREAM. Recibe el chat del frontend, llama al
// Orchestrator (AgentCore Runtime) firmando con SigV4 usando el ROL de esta Lambda y
// reenvia los eventos SSE token a token. Asi el navegador nunca ve credenciales AWS.
// Sin dependencias: solo node:crypto y fetch (Node 22).

import crypto from "node:crypto";

const REGION = process.env.AWS_REGION || "us-east-1";
const ORCH_ARN = process.env.ORCHESTRATOR_ARN || "";
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || "*";
const SERVICE = "bedrock-agentcore";

const hmac = (key, data) => crypto.createHmac("sha256", key).update(data).digest();
const sha256hex = (data) => crypto.createHash("sha256").update(data).digest("hex");

// --- SigV4 (verificado contra botocore en los tests) ---------------------------------
export function signRequest({ method, host, path, query = "", headers, body, creds, region, service, now = new Date() }) {
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, ""); // 20261123T180000Z
  const dateStamp = amzDate.slice(0, 8);
  const all = { ...headers, host, "x-amz-date": amzDate };
  if (creds.sessionToken) all["x-amz-security-token"] = creds.sessionToken;

  const names = Object.keys(all).map((k) => k.toLowerCase()).sort();
  const lower = Object.fromEntries(Object.entries(all).map(([k, v]) => [k.toLowerCase(), String(v).trim()]));
  const canonicalHeaders = names.map((n) => `${n}:${lower[n]}\n`).join("");
  const signedHeaders = names.join(";");
  // Servicios no-S3: el path (ya codificado) se vuelve a codificar una vez.
  const canonicalUri = path.split("/").map((s) => encodeURIComponent(s)).join("/");
  const canonicalRequest = [method, canonicalUri, query, canonicalHeaders, signedHeaders, sha256hex(body)].join("\n");

  const scope = `${dateStamp}/${region}/${service}/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", amzDate, scope, sha256hex(canonicalRequest)].join("\n");
  let k = hmac("AWS4" + creds.secretAccessKey, dateStamp);
  k = hmac(k, region); k = hmac(k, service); k = hmac(k, "aws4_request");
  const signature = crypto.createHmac("sha256", k).update(stringToSign).digest("hex");

  const out = { ...lower };
  delete out.host;
  out.authorization = `AWS4-HMAC-SHA256 Credential=${creds.accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
  return out;
}

// --- Validacion de entrada ------------------------------------------------------------
export function buildPayload(raw) {
  let b;
  try { b = JSON.parse(raw || "{}"); } catch { return { error: "JSON invalido" }; }
  const prompt = typeof b.prompt === "string" ? b.prompt.trim() : "";
  if (!prompt) return { error: "Falta 'prompt'" };
  if (prompt.length > 2000) return { error: "El mensaje es demasiado largo (max 2000 caracteres)" };
  const userId = String(b.userId || "demo-user").slice(0, 64);
  let sessionId = String(b.sessionId || "").slice(0, 100);
  if (sessionId.length < 33) sessionId = `${sessionId || "session"}-${crypto.randomUUID()}`; // AgentCore exige >= 33
  const history = Array.isArray(b.history) ? b.history.slice(-20) : [];
  const context = b.context && typeof b.context === "object" ? b.context : {};
  return { payload: { prompt, userId, sessionId, history, context }, sessionId };
}

const cors = () => ({
  "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Max-Age": "86400",
});

const streamify = globalThis.awslambda?.streamifyResponse ?? ((fn) => fn);
const httpStream = (s, meta) => (globalThis.awslambda ? globalThis.awslambda.HttpResponseStream.from(s, meta) : s);

const sse = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

export const handler = streamify(async (event, responseStream) => {
  const method = event?.requestContext?.http?.method || "POST";
  if (method === "OPTIONS") {
    // Preflight CORS. Con RESPONSE_STREAM, si no se escribe nada antes de end(), Lambda ignora el estado y las
    // cabeceras y responde 200 application/octet-stream sin CORS (el navegador bloquea el chat). Por eso se
    // escribe un cuerpo minimo y se responde 200 (un 204 no admite cuerpo).
    responseStream = httpStream(responseStream, { statusCode: 200, headers: { ...cors(), "Content-Type": "text/plain; charset=utf-8" } });
    responseStream.write("ok");
    responseStream.end();
    return;
  }

  const raw = event.isBase64Encoded ? Buffer.from(event.body || "", "base64").toString("utf8") : event.body;
  const { payload, sessionId, error } = buildPayload(raw);
  const baseHeaders = { ...cors(), "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" };

  if (error || !ORCH_ARN) {
    responseStream = httpStream(responseStream, { statusCode: error ? 400 : 500, headers: baseHeaders });
    responseStream.write(sse({ type: "error", message: error || "Falta ORCHESTRATOR_ARN en la Lambda" }));
    responseStream.end();
    return;
  }

  const host = `bedrock-agentcore.${REGION}.amazonaws.com`;
  const path = `/runtimes/${encodeURIComponent(ORCH_ARN)}/invocations`;
  const body = JSON.stringify(payload);
  const headers = signRequest({
    method: "POST", host, path, query: "", body, region: REGION, service: SERVICE,
    creds: {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
      sessionToken: process.env.AWS_SESSION_TOKEN,
    },
    headers: {
      "content-type": "application/json",
      "x-amzn-bedrock-agentcore-runtime-session-id": sessionId,
    },
  });

  responseStream = httpStream(responseStream, { statusCode: 200, headers: baseHeaders });
  try {
    const upstream = await fetch(`https://${host}${path}`, { method: "POST", headers, body });
    if (!upstream.ok) {
      const text = (await upstream.text()).slice(0, 600);
      console.error(`AgentCore respondio ${upstream.status}: ${text}`);          // el detalle tecnico queda en CloudWatch, no en pantalla
      responseStream.write(sse({ type: "error", message: `El asistente no pudo responder en este momento (código ${upstream.status}). Intenta de nuevo en unos segundos.` }));
      responseStream.write(sse({ type: "done" }));
    } else {
      for await (const chunk of upstream.body) responseStream.write(chunk);
    }
  } catch (err) {
    console.error(`No se pudo contactar al agente: ${err.message}`);
    responseStream.write(sse({ type: "error", message: "No se pudo contactar al asistente. Revisa tu conexión e intenta de nuevo." }));
    responseStream.write(sse({ type: "done" }));
  }
  responseStream.end();
});
