"""
configure_agentcore.py  -  agrega/actualiza el OrchestratorAgent en agentcore/agentcore.json
con las URLs REALES de Flights y Weather y el ID de la memoria (los descubre solo con AWS CLI).

Uso (desde la carpeta del proyecto, en la TERMINAL):
    python configure_agentcore.py
Opcional, si tu AWS CLI no tiene 'bedrock-agentcore-control':
    python configure_agentcore.py --flights-id tripplannera2a_FlightsAgent-XXXX --weather-id tripplannera2a_WeatherAgent-YYYY --memory-id tripplannera2a_TripPlannerMemory-ZZZZ
"""
import argparse, json, subprocess, sys, urllib.parse

REGION = "us-east-1"
PATH = "agentcore/agentcore.json"


def aws(*args):
    out = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"], capture_output=True, text=True, shell=(sys.platform == "win32"))
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return json.loads(out.stdout or "{}")


def find_runtime(prefix):
    items = aws("bedrock-agentcore-control", "list-agent-runtimes").get("agentRuntimes", [])
    for it in items:
        rid = it.get("agentRuntimeId", "")
        if rid.startswith(prefix):
            return rid, it.get("agentRuntimeArn")
    raise RuntimeError(f"No encontre un runtime que empiece por {prefix}. Runtimes: {[i.get('agentRuntimeId') for i in items]}")


def find_memory(prefix):
    items = aws("bedrock-agentcore-control", "list-memories").get("memories", [])
    for it in items:
        mid = it.get("id") or it.get("memoryId") or ""
        if mid.startswith(prefix):
            return mid
    return ""


def invoke_url(arn):
    return f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{urllib.parse.quote(arn, safe='')}/invocations"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flights-id"); ap.add_argument("--weather-id"); ap.add_argument("--memory-id")
    a = ap.parse_args()

    account = aws("sts", "get-caller-identity")["Account"]
    arn = lambda rid: f"arn:aws:bedrock-agentcore:{REGION}:{account}:runtime/{rid}"
    try:
        f_id, f_arn = (a.flights_id, arn(a.flights_id)) if a.flights_id else find_runtime("tripplannera2a_FlightsAgent")
        w_id, w_arn = (a.weather_id, arn(a.weather_id)) if a.weather_id else find_runtime("tripplannera2a_WeatherAgent")
        mem = a.memory_id if a.memory_id is not None else find_memory("tripplannera2a_TripPlannerMemory")
    except Exception as exc:
        print("No pude descubrir los IDs automaticamente:", exc)
        print("Corre 'agentcore status', copia los IDs y usa --flights-id/--weather-id/--memory-id.")
        sys.exit(1)

    j = json.load(open(PATH, encoding="utf-8-sig"))
    # el Orchestrator hereda el modelo elegido con set_model.py (se copia de FlightsAgent)
    src = next((r for r in j["runtimes"] if r["name"] == "FlightsAgent"), {})
    model_env = [e for e in src.get("envVars", []) if e["name"] in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL_ID", "BEDROCK_MODEL_ID")]
    if not any(e["name"] == "BEDROCK_MODEL_ID" for e in model_env) and not any(e["name"] == "LLM_PROVIDER" for e in model_env):
        model_env = [{"name": "BEDROCK_MODEL_ID", "value": "amazon.nova-pro-v1:0"}]
    orch = {
        "name": "OrchestratorAgent", "build": "CodeZip", "entrypoint": "main.py",
        "codeLocation": "app/OrchestratorAgent/", "runtimeVersion": "PYTHON_3_14",
        "networkMode": "PUBLIC", "protocol": "HTTP",
        "envVars": [
            {"name": "FLIGHTS_AGENT_URL", "value": invoke_url(f_arn)},
            {"name": "WEATHER_AGENT_URL", "value": invoke_url(w_arn)},
            {"name": "TRIP_MEMORY_ID", "value": mem},
        ] + model_env,
    }
    j["runtimes"] = [r for r in j["runtimes"] if r["name"] != "OrchestratorAgent"] + [orch]
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(j, f, indent=2); f.write("\n")
    print("OK. Runtimes:", [r["name"] for r in j["runtimes"]])
    print("Flights :", f_id); print("Weather :", w_id); print("Memoria :", mem or "(ninguna: el chat usara el historial del frontend)")


if __name__ == "__main__":
    main()
