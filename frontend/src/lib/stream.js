// Canal 2 (agentico): Frontend -> HTTP streaming (SSE) -> AgentCore Orchestrator.
// Sin API Gateway: el navegador habla con una Lambda Function URL que firma la
// llamada a AgentCore con su propio rol IAM (el navegador no tiene credenciales).
import { config } from "./config.js";

export async function streamChat(body, { onEvent, signal }) {
  if (!config.streamUrl) throw new Error("Falta STREAM_URL en config.js");
  let res;
  try {
    res = await fetch(config.streamUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("No se pudo conectar con el asistente. Revisa tu conexion.");
  }
  if (!res.body) throw new Error("El servidor no devolvio un stream");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const flush = (block) => {
    const data = block
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trim())
      .join("");
    if (!data) return;
    try {
      onEvent(JSON.parse(data));
    } catch {
      /* evento incompleto o no JSON: se ignora */
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let i;
    while ((i = buffer.indexOf("\n\n")) >= 0) {
      flush(buffer.slice(0, i));
      buffer = buffer.slice(i + 2);
    }
  }
  if (buffer.trim()) flush(buffer);
}
