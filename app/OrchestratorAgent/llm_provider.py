"""llm_provider.py - Modelo de lenguaje configurable para los agentes.

Por defecto los agentes usan Amazon Bedrock (variable BEDROCK_MODEL_ID).
Si LLM_PROVIDER=openai_compat usan cualquier API compatible con OpenAI
(Chat Completions con streaming y herramientas: Mistral, Gemini, Groq, OpenRouter...)
sin instalar dependencias extra: solo libreria estandar.

Variables de entorno del modo externo:
  LLM_PROVIDER    openai_compat
  LLM_BASE_URL    ej. https://api.mistral.ai/v1
  LLM_MODEL_ID    ej. mistral-small-latest
  LLM_API_KEY     (opcional) la clave; si no esta, se lee de AWS Secrets Manager
  LLM_SECRET_ID   (opcional) nombre del secreto, por defecto tripplanner/llm-api-key
  LLM_MAX_TOKENS  (opcional) limite de salida por respuesta, por defecto 8192
  LLM_TEMPERATURE (opcional) si no se define, no se envia y el proveedor usa la suya

Este archivo es identico en las tres carpetas de agentes (cada una se empaqueta por separado).
"""
import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from strands.models import Model

_RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)
_key_cache: dict = {}
_EXTRAS: dict = {}  # toolUseId -> extra_content (firmas de pensamiento de Gemini 3 que hay que devolver)


def get_api_key() -> str:
    """Clave del proveedor: variable de entorno o, si no esta, Secrets Manager."""
    env_key = os.environ.get("LLM_API_KEY", "").strip()
    if env_key:
        return env_key
    if "key" not in _key_cache:
        import boto3  # ya viene con strands-agents

        secret_id = os.environ.get("LLM_SECRET_ID", "tripplanner/llm-api-key")
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        value = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_id)["SecretString"]
        _key_cache["key"] = value.strip()
    return _key_cache["key"]


def build_model(default_bedrock_id: str = "amazon.nova-pro-v1:0"):
    """Devuelve el modelo para Agent(model=...): un id de Bedrock o un modelo compatible con OpenAI."""
    provider = os.environ.get("LLM_PROVIDER", "bedrock").strip().lower()
    if provider in ("openai_compat", "openai", "external"):
        return OpenAICompatModel(
            base_url=os.environ["LLM_BASE_URL"],
            model_id=os.environ["LLM_MODEL_ID"],
            api_key_fn=get_api_key,
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "8192")),  # Gemini cuenta aqui sus tokens de "pensamiento"
            temperature=float(os.environ["LLM_TEMPERATURE"]) if os.environ.get("LLM_TEMPERATURE") else None,
        )
    return os.environ.get("BEDROCK_MODEL_ID", default_bedrock_id)


# ---------------------------------------------------------------------------
# Conversion Strands -> OpenAI
# ---------------------------------------------------------------------------
def _simplify_schema(node: Any) -> Any:
    """Hace el JSON Schema mas simple para APIs estrictas (anyOf con null, titulos...)."""
    if isinstance(node, list):
        return [_simplify_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    node = dict(node)
    for key in ("anyOf", "oneOf"):
        if key in node:
            options = [o for o in node[key] if not (isinstance(o, dict) and o.get("type") == "null")]
            if len(options) == 1 and isinstance(options[0], dict):
                merged = {k: v for k, v in node.items() if k != key}
                merged.update(options[0])
                node = merged
            else:
                node[key] = options
    node.pop("title", None)
    node.pop("default", None)
    return {k: _simplify_schema(v) for k, v in node.items()}


def _to_openai_tools(tool_specs):
    tools = []
    for spec in tool_specs or []:
        s = spec.get("toolSpec", spec)
        schema = (s.get("inputSchema") or {}).get("json") or {"type": "object", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", "")[:1000],
                "parameters": _simplify_schema(schema),
            },
        })
    return tools


def _tool_result_text(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if "text" in item:
            parts.append(item["text"])
        elif "json" in item:
            parts.append(json.dumps(item["json"], ensure_ascii=False))
    text = "\n".join(parts) or "(sin contenido)"
    if result.get("status") == "error":
        text = "ERROR: " + text
    return text


def _to_openai_messages(messages, system_prompt):
    out = []
    names: dict = {}
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})
    for m in messages:
        blocks = m.get("content") or []
        texts, calls, results = [], [], []
        for b in blocks:
            if "text" in b:
                texts.append(b["text"])
            elif "toolUse" in b:
                tu = b["toolUse"]
                names[tu["toolUseId"]] = tu["name"]
                call = {
                    "id": tu["toolUseId"], "type": "function",
                    "function": {"name": tu["name"], "arguments": json.dumps(tu.get("input") or {}, ensure_ascii=False)},
                }
                if tu["toolUseId"] in _EXTRAS:
                    call["extra_content"] = _EXTRAS[tu["toolUseId"]]
                calls.append(call)
            elif "toolResult" in b:
                results.append(b["toolResult"])
        if m.get("role") == "assistant":
            content = "".join(texts)
            if not content and not calls:
                continue
            msg = {"role": "assistant"}
            msg["content"] = content if content else (None if not calls else "")
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for r in results:
                tool_msg = {"role": "tool", "tool_call_id": r["toolUseId"], "content": _tool_result_text(r)}
                if names.get(r["toolUseId"]):
                    tool_msg["name"] = names[r["toolUseId"]]
                out.append(tool_msg)
            if texts:
                out.append({"role": "user", "content": "".join(texts)})
    return out


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
class OpenAICompatModel(Model):
    """Modelo de Strands sobre Chat Completions (streaming + tools), solo libreria estandar."""

    def __init__(self, base_url, model_id, api_key_fn, temperature=None, max_tokens=4096, timeout=120, max_retries=8):
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self._api_key_fn = api_key_fn
        self.config = {"model_id": model_id, "temperature": temperature, "max_tokens": max_tokens}
        self.timeout = timeout
        self.max_retries = max_retries

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> Any:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("OpenAICompatModel no implementa structured_output")
        yield  # pragma: no cover

    # -- HTTP en un hilo, entregando los fragmentos SSE al bucle asyncio --
    def _sse_chunks(self, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        attempt = 0
        while True:
            req = urllib.request.Request(
                self.base_url + "/chat/completions", data=body, method="POST",
                headers={
                    "Content-Type": "application/json", "Accept": "text/event-stream",
                    "Authorization": "Bearer " + self._api_key_fn(), "User-Agent": "tripplanner-agent/1.0",
                },
            )
            try:
                resp = urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:500].decode("utf-8", "replace")
                if exc.code in _RETRY_STATUS and attempt < self.max_retries:
                    wait = _retry_after(exc) or min(2 ** attempt * 2, 30)
                    print(f"[llm] HTTP {exc.code}, reintento {attempt + 1}/{self.max_retries} en {wait:.0f}s")
                    time.sleep(wait)
                    attempt += 1
                    continue
                raise RuntimeError(f"El modelo respondio {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt * 2, 30))
                    attempt += 1
                    continue
                raise RuntimeError(f"No se pudo contactar al modelo: {exc}")
            with resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except ValueError:
                        continue
            return

    async def stream(self, messages, tool_specs=None, system_prompt=None, *, tool_choice=None, **kwargs):
        payload = {
            "model": self.model_id,
            "messages": _to_openai_messages(messages, system_prompt),
            "stream": True,
            "max_tokens": self.config.get("max_tokens", 4096),
        }
        if self.config.get("temperature") is not None:  # por defecto no se envia: Gemini 3 recomienda la temperatura de fabrica
            payload["temperature"] = self.config["temperature"]
        tools = _to_openai_tools(tool_specs)
        if tools:
            payload["tools"] = tools

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def worker():
            try:
                for chunk in self._sse_chunks(payload):
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
                loop.call_soon_threadsafe(queue.put_nowait, ("end", None))
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

        threading.Thread(target=worker, daemon=True).start()

        yield {"messageStart": {"role": "assistant"}}
        text_open = False
        calls: dict = {}
        finish = None
        usage = None
        while True:
            kind, value = await queue.get()
            if kind == "error":
                raise value
            if kind == "end":
                break
            if value.get("usage"):
                usage = value["usage"]
            for choice in value.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text_open = True
                    yield {"contentBlockDelta": {"delta": {"text": delta["content"]}}}
                for tc in delta.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    idx = tc.get("index")
                    if idx is None:  # algunas APIs (Gemini) omiten index e id: cada fragmento con nombre es una llamada nueva
                        new_call = tc.get("id") or not calls or (fn.get("name") and calls[max(calls)]["name"])
                        idx = len(calls) if new_call else max(calls)
                    c = calls.setdefault(idx, {"id": None, "name": "", "args": "", "extra": None})
                    if tc.get("extra_content"):
                        c["extra"] = tc["extra_content"]
                    if tc.get("id"):
                        c["id"] = tc["id"]
                    if fn.get("name") and not c["name"]:
                        c["name"] = fn["name"]
                    if fn.get("arguments"):
                        c["args"] += fn["arguments"] if isinstance(fn["arguments"], str) else json.dumps(fn["arguments"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]

        if text_open:
            yield {"contentBlockStop": {}}
        for idx in sorted(calls):
            c = calls[idx]
            if not c["name"]:
                continue
            tool_id = c["id"] or uuid.uuid4().hex[:9]
            if c.get("extra"):
                if len(_EXTRAS) > 500:
                    _EXTRAS.clear()
                _EXTRAS[tool_id] = c["extra"]
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_id, "name": c["name"]}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": c["args"] or "{}"}}}}
            yield {"contentBlockStop": {}}
        if any(c["name"] for c in calls.values()):
            reason = "tool_use"
        elif finish == "length":
            reason = "max_tokens"
        else:
            reason = "end_turn"
        yield {"messageStop": {"stopReason": reason}}
        if usage:
            yield {"metadata": {
                "usage": {"inputTokens": usage.get("prompt_tokens", 0), "outputTokens": usage.get("completion_tokens", 0),
                          "totalTokens": usage.get("total_tokens", 0)},
                "metrics": {"latencyMs": 0},
            }}


def _retry_after(exc) -> float:
    try:
        return min(float(exc.headers.get("Retry-After", "")), 60.0)
    except (TypeError, ValueError):
        return 0.0
