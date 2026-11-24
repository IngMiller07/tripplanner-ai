import { signRequest } from "./lambdas/stream_chat/index.mjs";
const arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tripplannera2a_OrchestratorAgent-AbC123xyz9";
const body = JSON.stringify({ prompt: "hola ñandú", userId: "miller" });
const h = signRequest({
  method: "POST", host: "bedrock-agentcore.us-east-1.amazonaws.com",
  path: `/runtimes/${encodeURIComponent(arn)}/invocations`, query: "", body,
  region: "us-east-1", service: "bedrock-agentcore",
  creds: { accessKeyId: "AKIDEXAMPLE", secretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", sessionToken: "TOKEN/abc+def==" },
  headers: { "content-type": "application/json", "x-amzn-bedrock-agentcore-runtime-session-id": "s".repeat(40) },
  now: new Date("2026-11-23T18:00:00Z"),
});
console.log(JSON.stringify({ auth: h.authorization, date: h["x-amz-date"] }));
