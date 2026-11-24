# deploy-all.ps1
# Despliega TripPlanner AI completo y en orden, verificando cada paso.
#   1 Revision previa      2 Especialistas y memoria   3 Backend
#   4 Orchestrator         5 Streaming                 6 Pruebas rapidas
#   7 Frontend
# Ejecutar en la TERMINAL, desde la raiz del proyecto (la carpeta que contiene "agentcore"):
#     powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1
# Retomar desde un paso (por ejemplo el 4):
#     powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1 -From 4
# Opcional, al final: limitar CORS del streaming al sitio:
#     powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1 -From 7 -RestrictCors
# Elegir el modelo de lenguaje (por defecto: Bedrock Nova Pro):
#     .\deploy-all.ps1 -ModelId us.amazon.nova-lite-v1:0                      (otro modelo/perfil de Bedrock)
#     .\deploy-all.ps1 -LlmBaseUrl "https://.../openai" -LlmModel "modelo"     (API externa compatible con OpenAI;
#                                                                              la clave se te pide sin mostrarla y se guarda en Secrets Manager)
# Todo queda registrado en deploy.log

param(
  [int]$From = 1,
  [switch]$RestrictCors,
  [string]$ModelId = "",
  [string]$LlmBaseUrl = "",
  [string]$LlmModel = ""
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$REGION = "us-east-1"
$STACK = "tripplanner-backend"
$ORCH_PREFIX = "tripplannera2a_OrchestratorAgent"
try { Start-Transcript -Path (Join-Path $PSScriptRoot "deploy.log") -Append | Out-Null } catch {}
$t0 = Get-Date

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Step($n, $title) { Say ""; Say "=== PASO $n de 7: $title ===" "Cyan" }
function Finish($code) { try { Stop-Transcript | Out-Null } catch {}; exit $code }
function Fail($msg) {
  Say ""
  Say "ERROR: $msg" "Red"
  Say "Copia las ultimas 20 lineas de esta ventana y mandamelas." "Yellow"
  Say "Para retomar sin repetir lo anterior:  .\deploy-all.ps1 -From <numero de paso>" "Yellow"
  Finish 1
}
function Run($what, [scriptblock]$cmd) {
  & $cmd
  if ($LASTEXITCODE -ne 0) { Fail "$what fallo (codigo $LASTEXITCODE)" }
}

function Get-OrchArn {
  $v = "$(aws bedrock-agentcore-control list-agent-runtimes --region $REGION --query "agentRuntimes[?starts_with(agentRuntimeId,'$ORCH_PREFIX')].agentRuntimeArn | [0]" --output text 2>$null)".Trim()
  if ($v -like "arn:aws:bedrock-agentcore*") { return $v }
  return ""
}

function StackOut($key) {
  $v = "$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue | [0]" --output text 2>$null)".Trim()
  if ($v -eq "None") { return "" }
  return $v
}

function Wait-Ready($prefix, $maxMinutes) {
  $deadline = (Get-Date).AddMinutes($maxMinutes)
  while ((Get-Date) -lt $deadline) {
    $st = "$(aws bedrock-agentcore-control list-agent-runtimes --region $REGION --query "agentRuntimes[?starts_with(agentRuntimeId,'$prefix')].status | [0]" --output text 2>$null)".Trim()
    if ($st -eq "READY") { Say "  $prefix : READY" "Green"; return }
    if ($st -like "*FAILED*") { Fail "$prefix quedo en estado $st. Mira los logs en CloudWatch: /aws/bedrock-agentcore/runtimes/<id>-DEFAULT" }
    Say "  $prefix : $st ... esperando"
    Start-Sleep -Seconds 15
  }
  Fail "$prefix no llego a READY en $maxMinutes minutos"
}

function Deploy-Backend([string]$OrchArn, [string]$Origin) {
  $st = "$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].StackStatus" --output text 2>$null)".Trim()
  if ($st -eq "ROLLBACK_COMPLETE" -or $st -eq "CREATE_FAILED") {
    Say "El stack $STACK esta en $st. Lo elimino para recrearlo limpio..." "Yellow"
    aws cloudformation delete-stack --stack-name $STACK --region $REGION
    aws cloudformation wait stack-delete-complete --stack-name $STACK --region $REGION
  }
  Push-Location (Join-Path $PSScriptRoot "backend")
  sam build
  if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "sam build fallo" }
  $samArgs = @("deploy", "--stack-name", $STACK, "--region", $REGION, "--resolve-s3", "--capabilities", "CAPABILITY_IAM", "--no-confirm-changeset", "--no-fail-on-empty-changeset")
  $over = @()
  if ($OrchArn) { $over += "OrchestratorArn=$OrchArn" }
  if ($Origin) { $over += "AllowedOrigin=$Origin" }
  if ($over.Count -gt 0) { $samArgs += "--parameter-overrides"; $samArgs += $over }
  sam @samArgs
  $code = $LASTEXITCODE
  Pop-Location
  if ($code -ne 0) { Fail "sam deploy fallo (codigo $code)" }
}

function Api-Call($method, $url, $json) {
  try {
    if ($json) { return Invoke-RestMethod -Method $method -Uri $url -ContentType "application/json" -Body $json -TimeoutSec 30 }
    return Invoke-RestMethod -Method $method -Uri $url -TimeoutSec 30
  } catch {
    Fail "$method $url fallo: $($_.Exception.Message)"
  }
}

# ---------------------------------------------------------------- 1. Revision previa
Step 1 "Revision previa"
foreach ($c in @("aws", "python", "node", "npm", "sam", "agentcore")) {
  if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { Fail "No encuentro '$c' en el PATH. Instalalo o abre una terminal nueva." }
}
if (-not (Test-Path "agentcore\agentcore.json")) { Fail "No veo agentcore\agentcore.json. Ejecuta este script desde la raiz del proyecto (la carpeta que contiene 'agentcore')." }
foreach ($p in @("backend\template.yaml", "frontend\package.json", "set_model.py", "app\OrchestratorAgent\main.py", "app\OrchestratorAgent\llm_provider.py", "app\FlightsAgent\main.py", "app\FlightsAgent\llm_provider.py", "app\WeatherAgent\main.py", "app\WeatherAgent\llm_provider.py")) {
  if (-not (Test-Path $p)) { Fail "Falta $p. Descomprime el zip del proyecto dentro de esta carpeta." }
}
if (-not (Test-Path "app\OrchestratorAgent\pyproject.toml")) { Fail "Falta app\OrchestratorAgent\pyproject.toml (lo crea el CLI de AgentCore). Mandame el resultado de: dir app\OrchestratorAgent" }

python -c "import ast,sys; [ast.parse(open(f,encoding='utf-8-sig').read(), f) for f in sys.argv[1:]]; print('Sintaxis de los agentes: OK')" app/FlightsAgent/main.py app/WeatherAgent/main.py app/WeatherAgent/weather_mcp_server.py app/OrchestratorAgent/main.py app/OrchestratorAgent/llm_provider.py configure_agentcore.py set_model.py
if ($LASTEXITCODE -ne 0) { Fail "Hay un error de sintaxis en un archivo de los agentes (mira el mensaje de arriba)." }

$ACCOUNT = "$(aws sts get-caller-identity --query Account --output text 2>$null)".Trim()
if ($ACCOUNT -notmatch '^\d{12}$') { Fail "AWS CLI sin credenciales validas. Corre: aws configure" }
Say "Cuenta AWS: $ACCOUNT   Region: $REGION" "Green"

$bpa = "$(aws s3control get-public-access-block --account-id $ACCOUNT --query "PublicAccessBlockConfiguration.[BlockPublicPolicy,RestrictPublicBuckets]" --output text 2>$null)"
if ($bpa -match "True") {
  Say "La cuenta bloquea buckets publicos a nivel de cuenta; el sitio web de S3 no funcionaria." "Red"
  Say "Corre esto y vuelve a ejecutar el script:" "Yellow"
  Say "aws s3control put-public-access-block --account-id $ACCOUNT --public-access-block-configuration BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"
  Fail "Block Public Access activo en la cuenta"
}

# --- Agentes registrados en agentcore.json (agrega WeatherAgent si falta)
Run "set_model.py --ensure" { python set_model.py --ensure }

# --- Modelo de lenguaje: Bedrock (por defecto) o una API externa compatible con OpenAI
if ($LlmBaseUrl -or $LlmModel) {
  if (-not ($LlmBaseUrl -and $LlmModel)) { Fail "Para un modelo externo necesitas -LlmBaseUrl y -LlmModel juntos." }
  Run "set_model.py" { python set_model.py --provider external --base-url $LlmBaseUrl --llm-model $LlmModel }
} elseif ($ModelId) {
  Run "set_model.py" { python set_model.py --provider bedrock --model-id $ModelId }
}
$cfg = @{}
foreach ($line in @(python set_model.py --show)) { if ("$line" -match '^(\w+)=(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() } }

if ($cfg["provider"] -eq "external") {
  Say "Modelo: API externa  $($cfg['llm_model'])  en  $($cfg['base_url'])" "Cyan"
  $SECRET = "tripplanner/llm-api-key"
  $have = "$(aws secretsmanager describe-secret --secret-id $SECRET --region $REGION --query Name --output text 2>$null)".Trim()
  $key = "$env:LLM_API_KEY"   # opcional: asi se puede pasar sin teclear (nunca como parametro: quedaria en deploy.log)
  if (-not $key -and $have -ne $SECRET) {
    $sec = Read-Host "Pega la API key del modelo (no se mostrara en pantalla)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  }
  if ($key) {
    $key = $key.Trim()
    if (-not $key) { Fail "La API key esta vacia." }
    $kf = Join-Path ([System.IO.Path]::GetTempPath()) "tp_llmkey.txt"
    [System.IO.File]::WriteAllText($kf, $key)
    if ($have -eq $SECRET) { aws secretsmanager put-secret-value --secret-id $SECRET --secret-string "file://$kf" --region $REGION | Out-Null }
    else { aws secretsmanager create-secret --name $SECRET --secret-string "file://$kf" --region $REGION | Out-Null }
    $code = $LASTEXITCODE
    Remove-Item $kf -Force -ErrorAction SilentlyContinue
    if ($code -ne 0) { Fail "No pude guardar la clave en AWS Secrets Manager (codigo $code)." }
    Say "Clave guardada en AWS Secrets Manager ($SECRET). No queda en ningun archivo del proyecto." "Green"
  } else {
    $key = "$(aws secretsmanager get-secret-value --secret-id $SECRET --region $REGION --query SecretString --output text 2>$null)".Trim()
    if (-not $key) { Fail "No pude leer la clave guardada en Secrets Manager. Vuelve a ejecutar el script: te la pedira de nuevo." }
    Say "Uso la clave que ya estaba en Secrets Manager." "Gray"
  }
  $env:LLM_API_KEY = $key
  python set_model.py --test-external --base-url $cfg["base_url"] --llm-model $cfg["llm_model"]
  $tcode = $LASTEXITCODE
  if ($tcode -eq 1 -or $tcode -eq 4) {
    Say "Para ver los modelos que tu clave puede usar:" "Yellow"
    Say "  `$env:LLM_API_KEY = '<tu clave>'; python set_model.py --list-models --base-url $($cfg['base_url'])" "Yellow"
  }
  Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
  if ($tcode -eq 3) { Say "Sigo adelante: la clave es valida (solo estaba en el limite de uso)." "Yellow" }
  elseif ($tcode -ne 0) { Fail "El modelo externo no esta listo (ver detalle arriba)." }
} else {
  $mid = $cfg["model_id"]; if (-not $mid) { $mid = "amazon.nova-pro-v1:0" }
  $msgFile = Join-Path ([System.IO.Path]::GetTempPath()) "tp_msgs.json"
  [System.IO.File]::WriteAllText($msgFile, '[{"role":"user","content":[{"text":"Responde solo: ok"}]}]')
  aws bedrock-runtime converse --model-id $mid --messages "file://$msgFile" --region $REGION --query "output.message.content[0].text" --output text 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    aws bedrock-runtime converse --model-id $mid --messages "file://$msgFile" --region $REGION --query "output.message.content[0].text" --output text
    Say ""
    Say "Bedrock no te deja usar $mid en esta cuenta (si dice 'Too many tokens per day', las cuotas estan en 0: pasa en cuentas nuevas)." "Red"
    Say "Opciones:" "Yellow"
    Say "  1) Usar otra cuenta de AWS con Bedrock activo:   aws configure" "Yellow"
    Say "  2) Otro modelo de Bedrock:   .\deploy-all.ps1 -ModelId us.amazon.nova-lite-v1:0" "Yellow"
    Say "  3) Un modelo externo gratuito (ver docs/MODELO_EXTERNO.md):" "Yellow"
    Say "     .\deploy-all.ps1 -LlmBaseUrl https://generativelanguage.googleapis.com/v1beta/openai -LlmModel <modelo>" "Yellow"
    Fail "No pude usar el modelo $mid en $REGION."
  }
  Say "Modelo de Bedrock ($mid): OK" "Green"
  $q = "$(aws service-quotas list-service-quotas --service-code bedrock --region $REGION --query "Quotas[?contains(QuotaName,'Nova Pro') && contains(QuotaName,'requests per minute')].[QuotaName,Value]" --output text 2>$null)"
  if ($q.Trim()) { Say "Cuotas de Nova Pro (solicitudes por minuto):"; Say $q.Trim() }
}

# ---------------------------------------------------------------- 2. Especialistas y memoria
if ($From -le 2) {
  Step 2 "Especialistas (Flights y Weather) y memoria"
  Run "agentcore deploy" { agentcore deploy -y }
  Wait-Ready "tripplannera2a_FlightsAgent" 10
  Wait-Ready "tripplannera2a_WeatherAgent" 10
}

# ---------------------------------------------------------------- 3. Backend
if ($From -le 3) {
  Step 3 "Backend (DynamoDB, Lambdas, API Gateway y bucket del frontend)"
  Deploy-Backend (Get-OrchArn) ""
  if (-not (StackOut "ApiUrl")) { Fail "El stack no devolvio ApiUrl." }
  Say "Backend desplegado. API: $(StackOut 'ApiUrl')" "Green"
}

# ---------------------------------------------------------------- 4. Orchestrator
if ($From -le 4) {
  Step 4 "Orchestrator"
  Run "configure_agentcore.py" { python configure_agentcore.py }
  Run "agentcore deploy (Orchestrator)" { agentcore deploy -y }
  Wait-Ready $ORCH_PREFIX 15
  Run "setup-iam.ps1" { powershell -NoProfile -ExecutionPolicy Bypass -File ./setup-iam.ps1 }
}

# ---------------------------------------------------------------- 5. Streaming
if ($From -le 5) {
  Step 5 "Streaming (Lambda Function URL hacia el Orchestrator)"
  $orch = Get-OrchArn
  if (-not $orch) { Fail "No encuentro el runtime OrchestratorAgent. Corre el paso 4:  .\deploy-all.ps1 -From 4" }
  Deploy-Backend $orch ""
  if (-not (StackOut "StreamUrl")) { Fail "El stack no devolvio StreamUrl." }
  Say "Streaming listo: $(StackOut 'StreamUrl')" "Green"
}

# ---------------------------------------------------------------- 6. Pruebas rapidas
if ($From -le 6) {
  Step 6 "Pruebas rapidas"
  $api = (StackOut "ApiUrl").TrimEnd('/')
  $stream = StackOut "StreamUrl"
  if (-not $api) { Fail "No hay ApiUrl. Corre el paso 3." }

  $trip = Api-Call "Post" "$api/trips" '{"userId":"smoke-test","origin":"Bogota","destination":"Cartagena","startDate":"2026-12-10","endDate":"2026-12-14","travelers":3,"budget":5000000}'
  if (-not $trip.tripId) { Fail "POST /trips no devolvio tripId" }
  $tid = $trip.tripId
  $null = Api-Call "Put" "$api/trips/$tid" '{"status":"RESERVADO"}'
  $list = Api-Call "Get" "$api/trips?userId=smoke-test" $null
  if (-not $list.trips -or @($list.trips).Count -lt 1) { Fail "GET /trips no devolvio el viaje recien creado" }
  $it = Api-Call "Post" "$api/itineraries" ('{"tripId":"' + $tid + '","days":[{"day":1,"title":"Llegada","activities":["Check-in","Cena"]}]}')
  if (-not $it.itineraryId) { Fail "POST /itineraries no devolvio itineraryId" }
  $null = Api-Call "Put" "$api/itineraries/$tid/$($it.itineraryId)" '{"days":[{"day":1,"title":"Llegada","activities":["Check-in","Cena","Paseo"]}]}'
  $res = Api-Call "Post" "$api/reservations" ('{"tripId":"' + $tid + '","type":"VUELO","details":{"airline":"Prueba","price_usd":100}}')
  if (-not $res.reservationId) { Fail "POST /reservations no devolvio reservationId" }
  $upd = Api-Call "Put" "$api/reservations/$tid/$($res.reservationId)" '{"status":"CONFIRMADA"}'
  if ($upd.status -ne "CONFIRMADA") { Fail "PUT /reservations no cambio el estado" }
  $del = Api-Call "Delete" "$api/reservations/$tid/$($res.reservationId)" $null
  if (-not $del.deleted) { Fail "DELETE /reservations no elimino la reserva" }
  Say "Canal 1 (API REST + Lambdas + DynamoDB): OK (viaje, itinerario, reserva y eliminacion de prueba)" "Green"

  if (-not $stream) {
    Say "Aun no hay StreamUrl: se omite la prueba del Canal 2." "Yellow"
  } elseif (-not (Get-Command "curl.exe" -ErrorAction SilentlyContinue)) {
    Say "No encuentro curl.exe: se omite la prueba del Canal 2 (pruebala desde el sitio web)." "Yellow"
  } else {
    $body = Join-Path ([System.IO.Path]::GetTempPath()) "tp_stream.json"
    [System.IO.File]::WriteAllText($body, '{"prompt":"Responde en una frase: que puedes hacer por mi?","userId":"smoke-test"}')
    $ok = $false
    for ($i = 1; $i -le 3 -and -not $ok; $i++) {
      Say "Canal 2 (agente por streaming), intento $i de 3 (la primera vez puede tardar por el arranque en frio)..."
      $out = "$(curl.exe -sS -N --max-time 170 -X POST $stream -H 'Content-Type: application/json' --data-binary "@$body" 2>&1)"
      if ($out -match '"type":\s*"done"' -and $out -notmatch '"type":\s*"error"') { $ok = $true } else {
        $shown = $out; if ($shown.Length -gt 700) { $shown = $shown.Substring(0, 700) }
        Say "  Respuesta: $shown" "Yellow"
        Start-Sleep -Seconds 12
      }
    }
    if ($ok) { Say "Canal 2 (Function URL -> Orchestrator): OK" "Green" }
    else { Say "ATENCION: el Canal 2 no respondio bien. Copia lo de arriba y mandamelo; el frontend se sube igual." "Red" }
  }
}

# ---------------------------------------------------------------- 7. Frontend
if ($From -le 7) {
  Step 7 "Frontend (S3)"
  Push-Location (Join-Path $PSScriptRoot "frontend")
  powershell -NoProfile -ExecutionPolicy Bypass -File ./deploy-frontend.ps1
  $code = $LASTEXITCODE
  Pop-Location
  if ($code -ne 0) { Fail "El despliegue del frontend fallo" }

  if ($RestrictCors) {
    $site = (StackOut "WebsiteUrl").TrimEnd('/')
    $orch = Get-OrchArn
    Say "Limitando CORS del streaming a $site ..." "Cyan"
    Deploy-Backend $orch $site
  }
}

# ---------------------------------------------------------------- Resumen
$mins = [int]((Get-Date) - $t0).TotalMinutes
Say ""
Say "=====================================================" "Green"
Say "LISTO en $mins minutos." "Green"
Say "Sitio web : $(StackOut 'WebsiteUrl')" "Green"
Say "API REST  : $(StackOut 'ApiUrl')"
Say "Streaming : $(StackOut 'StreamUrl')"
Say ""
Say "Siguiente: abre el sitio, entra con tu nombre, escribe 'hola' para calentar el agente"
Say "y luego usa la sugerencia 'Vacaciones de 4 dias en Cartagena'."
Say "=====================================================" "Green"
Finish 0
