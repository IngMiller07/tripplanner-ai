# Modelo de lenguaje: Bedrock o una API externa

Los tres agentes (Orchestrator, Flights y Weather) siguen corriendo en **AgentCore Runtime**, con sus herramientas, A2A y MCP. Lo único configurable es **qué modelo de lenguaje** responde:

| Modo | Cuándo usarlo | Configuración |
|---|---|---|
| **Bedrock** (por defecto) | Cuenta de AWS con cuotas de Bedrock activas | `BEDROCK_MODEL_ID`, por defecto `amazon.nova-pro-v1:0` |
| **API externa** compatible con OpenAI | Cuenta nueva con las cuotas de Bedrock en 0 (`ThrottlingException: Too many tokens per day`) | `LLM_PROVIDER=openai_compat`, `LLM_BASE_URL`, `LLM_MODEL_ID` |

La clave de una API externa se guarda en **AWS Secrets Manager** (`tripplanner/llm-api-key`). No se escribe en `agentcore.json`, en el repositorio ni en el navegador. Los tres roles de los agentes reciben permiso de lectura solo sobre ese secreto (`setup-iam.ps1`).

## Cómo funciona

`app/*/llm_provider.py` (el mismo archivo en cada agente) expone `build_model()`. Si `LLM_PROVIDER` es `bedrock`, devuelve el identificador del modelo y Strands usa Bedrock. Si es `openai_compat`, devuelve `OpenAICompatModel`, un proveedor de Strands que:

- usa el endpoint `POST {LLM_BASE_URL}/chat/completions` con streaming SSE y la biblioteca estándar de Python, sin dependencias nuevas;
- convierte mensajes, llamadas a herramientas y resultados entre el formato de Strands y el de OpenAI;
- reintenta los errores 429 y 5xx respetando `Retry-After`, para tolerar los límites de uso de los planes gratuitos;
- tolera las diferencias conocidas de Gemini (llamadas sin `index` ni `id`, firmas de pensamiento que hay que devolver).

Se verifica con `python tests/test_llm_provider.py`, que ejecuta un `Agent` real de Strands contra un servidor local que imita la API.

## Uso

Todos los comandos van en la **terminal**, desde la raíz del proyecto.

### Opción A: otro modelo de Bedrock

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1 -ModelId us.amazon.nova-lite-v1:0
```

### Opción B: API externa (Gemini como ejemplo)

1. Crea una clave en Google AI Studio (`aistudio.google.com`, «Get API key»). No pide tarjeta.
2. Mira qué modelos acepta tu clave. El nombre cambia con el tiempo, así que se consulta en lugar de adivinarlo:

```powershell
$env:LLM_API_KEY = "PEGA_AQUI_TU_CLAVE"
python set_model.py --list-models --base-url https://generativelanguage.googleapis.com/v1beta/openai
Remove-Item Env:LLM_API_KEY
```

3. Elige un modelo «flash» de la lista y despliega. El script pide la clave sin mostrarla, la guarda en Secrets Manager y comprueba que el modelo sabe usar herramientas:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1 -LlmBaseUrl https://generativelanguage.googleapis.com/v1beta/openai -LlmModel NOMBRE_DEL_MODELO
```

Otros proveedores con la misma forma de API: Mistral (`https://api.mistral.ai/v1`), Groq (`https://api.groq.com/openai/v1`), OpenRouter (`https://openrouter.ai/api/v1`) y Cerebras. Los planes gratuitos tienen límites por minuto y por día; Groq (unos 8 000 tokens por minuto en los modelos grandes) suele quedarse corto para este agente. El agente reintenta cuando recibe un 429, pero la primera respuesta puede tardar más.

### Volver a Bedrock

```powershell
python set_model.py --provider bedrock --model-id amazon.nova-pro-v1:0
powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1 -From 2
```

## Aviso

En el plan gratuito de algunos proveedores, el contenido enviado puede usarse para mejorar sus productos. El proyecto solo envía datos de viajes de demostración; no uses este modo con datos personales reales.
