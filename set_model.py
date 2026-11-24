"""
set_model.py - Elige el modelo de lenguaje de los 3 agentes (Bedrock o una API externa compatible con OpenAI)
y lo escribe en agentcore/agentcore.json. La clave de una API externa NUNCA va aqui: vive en AWS Secrets Manager.

Uso (en la TERMINAL, desde la raiz del proyecto):
  python set_model.py --ensure          (registra FlightsAgent y WeatherAgent en agentcore.json si faltan)
  python set_model.py --show
  python set_model.py --provider bedrock  --model-id amazon.nova-pro-v1:0
  python set_model.py --provider external --base-url https://generativelanguage.googleapis.com/v1beta/openai --llm-model gemini-2.5-flash
  python set_model.py --list-models --base-url <url>            (la clave se lee de la variable LLM_API_KEY)
  python set_model.py --test-external --base-url <url> --llm-model <modelo>   (idem)
"""
import argparse, json, os, sys, urllib.error, urllib.request

PATH = "agentcore/agentcore.json"
MANAGED = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL_ID", "BEDROCK_MODEL_ID")


def load():
    return json.load(open(PATH, encoding="utf-8-sig"))


def save(j):
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(j, f, indent=2); f.write("\n")


def env_of(rt):
    return {e["name"]: e["value"] for e in rt.get("envVars", [])}


def ensure_runtimes(j):
    """Garantiza que FlightsAgent y WeatherAgent esten registrados en agentcore.json.
    El de Weather se clona de FlightsAgent (mismo protocolo A2A y mismo esquema del CLI)."""
    names = [r["name"] for r in j["runtimes"]]
    added = []
    if "WeatherAgent" not in names:
        if "FlightsAgent" not in names:
            print("No encuentro FlightsAgent en agentcore.json; no puedo crear WeatherAgent a partir de el."); sys.exit(2)
        if not os.path.isfile("app/WeatherAgent/main.py"):
            print("Falta app/WeatherAgent/main.py."); sys.exit(2)
        base = next(r for r in j["runtimes"] if r["name"] == "FlightsAgent")
        clone = json.loads(json.dumps(base).replace("FlightsAgent", "WeatherAgent"))
        clone["envVars"] = []
        j["runtimes"].append(clone)
        added.append("WeatherAgent")
    return added


def ensure():
    j = load()
    added = ensure_runtimes(j)
    if added:
        save(j)
        print("Agregue a agentcore.json:", added)
    else:
        print("agentcore.json ya tiene FlightsAgent y WeatherAgent.")


def sync_account(account):
    """Apunta agentcore/aws-targets.json a la cuenta activa y aparta el estado de despliegue de la cuenta anterior."""
    import shutil
    path = "agentcore/aws-targets.json"
    if not os.path.isfile(path):
        print("No existe agentcore/aws-targets.json: el CLI lo creara solo en el primer despliegue."); return
    data = json.load(open(path, encoding="utf-8-sig"))
    targets = data if isinstance(data, list) else ([data] if "account" in data else data.get("targets", []))
    old = set()
    for t in targets:
        if isinstance(t, dict) and "account" in t and str(t["account"]) != account:
            old.add(str(t["account"])); t["account"] = account
    if not old:
        print("aws-targets.json ya apunta a la cuenta", account); return
    tag = sorted(old)[0]
    shutil.copy(path, path + ".bak-" + tag)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2); f.write("\n")
    print("aws-targets.json: cuenta", ", ".join(sorted(old)), "->", account, "(copia: aws-targets.json.bak-" + tag + ")")
    state = "agentcore/.cli/deployed-state.json"
    if os.path.isfile(state):
        os.replace(state, state + ".bak-" + tag)
        print("Aparte el estado de despliegue de la cuenta anterior (deployed-state.json.bak-" + tag + ").")


def show():
    j = load()
    src = next((r for r in j["runtimes"] if r["name"] == "FlightsAgent"), j["runtimes"][0])
    e = env_of(src)
    provider = "external" if e.get("LLM_PROVIDER", "bedrock") in ("openai_compat", "openai", "external") else "bedrock"
    print("provider=" + provider)
    print("model_id=" + e.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0"))
    print("base_url=" + e.get("LLM_BASE_URL", ""))
    print("llm_model=" + e.get("LLM_MODEL_ID", ""))


def apply(provider, model_id, base_url, llm_model):
    j = load()
    ensure_runtimes(j)
    for rt in j["runtimes"]:
        keep = [e for e in rt.get("envVars", []) if e["name"] not in MANAGED]
        if provider == "external":
            keep += [{"name": "LLM_PROVIDER", "value": "openai_compat"},
                     {"name": "LLM_BASE_URL", "value": base_url.rstrip("/")},
                     {"name": "LLM_MODEL_ID", "value": llm_model}]
        else:
            keep += [{"name": "LLM_PROVIDER", "value": "bedrock"},
                     {"name": "BEDROCK_MODEL_ID", "value": model_id}]
        rt["envVars"] = keep
    save(j)
    print("OK. Modelo configurado en:", [r["name"] for r in j["runtimes"]])
    print("Proveedor:", provider, "|", (llm_model + " @ " + base_url) if provider == "external" else model_id)


def http_json(url, key, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                          "User-Agent": "tripplanner-setup/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:600].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def http_json_retry(url, key, payload, tries=5):
    """Reintenta los errores temporales del servidor (500/502/503/504), p. ej. 'high demand' de Gemini."""
    import time
    code, body = 0, ""
    for i in range(tries):
        code, body = http_json(url, key, payload)
        if code not in (0, 500, 502, 503, 504):
            return code, body
        if i < tries - 1:
            wait = 4 * (i + 1)
            print(f"  El servicio esta saturado (HTTP {code}); reintento {i + 1} de {tries - 1} en {wait}s...", flush=True)
            time.sleep(wait)
    return code, body


def need_key():
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        print("Falta la clave (variable LLM_API_KEY)."); sys.exit(2)
    return key


def list_models(base_url):
    code, body = http_json(base_url.rstrip("/") + "/models", need_key())
    if code != 200:
        print(f"No pude listar modelos (HTTP {code}): {body}"); sys.exit(1)
    ids = sorted(m.get("id", "").replace("models/", "") for m in body.get("data", []))
    for i in ids:
        print(i)


def test_external(base_url, model):
    """Prueba en 2 vueltas, igual que el agente: (1) el modelo pide una herramienta, (2) se le devuelve el resultado."""
    key = need_key()
    url = base_url.rstrip("/") + "/chat/completions"
    tool = {"type": "function", "function": {"name": "sumar", "description": "Suma dos enteros.",
            "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}}
    user = {"role": "user", "content": "Usa la herramienta sumar para calcular 2+3 y dime el resultado."}
    code, body = http_json_retry(url, key, {"model": model, "max_tokens": 1024, "messages": [user], "tools": [tool]})
    if code in (0, 500, 502, 503, 504):
        print(f"El servicio del modelo sigue saturado o sin respuesta (HTTP {code}): {body}")
        print("Espera un minuto y repite, o prueba otro modelo (por ejemplo gemini-3.7-flash)."); sys.exit(5)
    if code == 429:
        print("La API respondio 429 (limite de uso). La clave es valida, pero espera un minuto o usa otro modelo."); sys.exit(3)
    if code != 200:
        print(f"La API respondio HTTP {code}: {body}"); sys.exit(1)
    msg = (body.get("choices") or [{}])[0].get("message", {})
    calls = msg.get("tool_calls") or []
    if not calls:
        print("El modelo responde pero NO uso la herramienta; no sirve para agentes. Elige otro modelo (--list-models)."); sys.exit(4)
    for i, c in enumerate(calls):
        c.setdefault("id", "call_%d" % i)
        c.setdefault("type", "function")
    echo = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
    results = [{"role": "tool", "tool_call_id": c["id"], "name": c["function"]["name"], "content": "5"} for c in calls]
    code, body = http_json_retry(url, key, {"model": model, "max_tokens": 1024, "messages": [user, echo] + results, "tools": [tool]})
    if code in (0, 500, 502, 503, 504):
        print(f"El servicio del modelo sigue saturado en la segunda vuelta (HTTP {code}): {body}")
        print("Espera un minuto y repite, o prueba otro modelo (por ejemplo gemini-3.7-flash)."); sys.exit(5)
    if code == 429:
        print("Primera vuelta OK; la segunda dio 429 (limite de uso). La clave es valida."); sys.exit(3)
    if code != 200:
        print(f"La primera vuelta funciono pero la SEGUNDA (devolver el resultado de la herramienta) fallo, HTTP {code}: {body}"); sys.exit(1)
    print("OK: el modelo responde, usa herramientas y acepta el resultado (2 vueltas).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--sync-account", default="")
    ap.add_argument("--provider", choices=["bedrock", "external"])
    ap.add_argument("--model-id", default="amazon.nova-pro-v1:0")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--llm-model", default="")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--test-external", action="store_true")
    a = ap.parse_args()
    if a.sync_account:
        sync_account(a.sync_account)
    elif a.ensure:
        ensure()
    elif a.show:
        show()
    elif a.list_models:
        list_models(a.base_url)
    elif a.test_external:
        test_external(a.base_url, a.llm_model)
    elif a.provider == "external":
        if not (a.base_url and a.llm_model):
            print("Para el modo externo necesitas --base-url y --llm-model."); sys.exit(2)
        apply("external", a.model_id, a.base_url, a.llm_model)
    elif a.provider == "bedrock":
        apply("bedrock", a.model_id, "", "")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
