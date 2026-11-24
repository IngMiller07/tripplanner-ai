# setup-iam.ps1  (v4 - descubre todo solo, no hay que pegar IDs)
#
# Da al rol de ejecucion del OrchestratorAgent los permisos que necesita:
#   1) invocar a los especialistas A2A (Flights y Weather)
#   2) usar la memoria gestionada (TripPlannerMemory)
#   3) invocar las 9 Lambdas de negocio (Tools de viajes/itinerarios/reservas)
# Y a los roles de los TRES agentes:
#   4) invocar modelos de Bedrock (incluye perfiles de inferencia entre regiones "us.*")
#   5) leer la clave del modelo externo en Secrets Manager (solo si usas un proveedor externo)
# Es idempotente: corrigelo cuantas veces quieras (por ejemplo tras un redeploy de AgentCore,
# porque CDK puede recrear el rol y borrar las politicas en linea).
#
# Requisito: haber corrido "agentcore deploy -y" con el OrchestratorAgent.
# Ejecutar en la TERMINAL, desde la carpeta del proyecto.

$ErrorActionPreference = "Stop"
$REGION = "us-east-1"

$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text).Trim()
Write-Host "Cuenta: $ACCOUNT_ID"

$ROLE = (aws iam list-roles --query "Roles[?contains(RoleName,'AgentCore-tripplannera2a') && contains(RoleName,'Orchestra')].RoleName | [0]" --output text).Trim()
if (-not $ROLE -or $ROLE -eq "None") {
  Write-Host "No encontre el rol del Orchestrator. Asegurate de haber desplegado el OrchestratorAgent (agentcore deploy -y)." -ForegroundColor Red
  exit 1
}
Write-Host "Rol del Orchestrator: $ROLE"

$tmp = $env:TEMP

function Apply-Policy($name, $json, $role = $ROLE) {
  $file = Join-Path $tmp ("$name.json")
  [System.IO.File]::WriteAllText($file, $json)
  aws iam put-role-policy --role-name $role --policy-name $name --policy-document "file://$file"
  if ($LASTEXITCODE -ne 0) { throw "Fallo al aplicar la politica $name en $role" }
  Write-Host "OK: $name ($role)"
}

# 1) Invocar los especialistas A2A (se usan comodines: no depende de los IDs aleatorios)
$a2a = @'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Action":["bedrock-agentcore:InvokeAgentRuntime","bedrock-agentcore:GetAgentCard"],
 "Resource":["arn:aws:bedrock-agentcore:__REGION__:__ACCOUNT__:runtime/tripplannera2a_FlightsAgent-*",
             "arn:aws:bedrock-agentcore:__REGION__:__ACCOUNT__:runtime/tripplannera2a_WeatherAgent-*"]}]}
'@
Apply-Policy "InvokeA2ASpecialists" $a2a.Replace("__REGION__", $REGION).Replace("__ACCOUNT__", $ACCOUNT_ID)

# 2) Memoria gestionada
$mem = @'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Action":["bedrock-agentcore:CreateEvent","bedrock-agentcore:GetEvent","bedrock-agentcore:ListEvents",
           "bedrock-agentcore:ListActors","bedrock-agentcore:ListSessions","bedrock-agentcore:RetrieveMemoryRecords",
           "bedrock-agentcore:GetMemoryRecord","bedrock-agentcore:ListMemoryRecords","bedrock-agentcore:GetMemory"],
 "Resource":["arn:aws:bedrock-agentcore:__REGION__:__ACCOUNT__:memory/tripplannera2a_TripPlannerMemory-*"]}]}
'@
Apply-Policy "UseTripPlannerMemory" $mem.Replace("__REGION__", $REGION).Replace("__ACCOUNT__", $ACCOUNT_ID)

# 3) Las 9 Lambdas de negocio que invoca el agente (privilegio minimo: solo esas funciones, solo invocar)
$lam = @'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"lambda:InvokeFunction",
 "Resource":[
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-CreateTrip",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-GetTrips",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-UpdateTrip",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-CreateItinerary",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-UpdateItinerary",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-CreateReservation",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-GetReservations",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-UpdateReservation",
  "arn:aws:lambda:__REGION__:__ACCOUNT__:function:TripPlanner-DeleteReservation"]}]}
'@
Apply-Policy "InvokeBusinessLambdas" $lam.Replace("__REGION__", $REGION).Replace("__ACCOUNT__", $ACCOUNT_ID)

# 4 y 5) Modelo: Bedrock + clave del proveedor externo, para los 3 agentes
$bedrock = @'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream","bedrock:Converse","bedrock:ConverseStream"],
 "Resource":["arn:aws:bedrock:*::foundation-model/*",
             "arn:aws:bedrock:*:__ACCOUNT__:inference-profile/*",
             "arn:aws:bedrock:*:__ACCOUNT__:application-inference-profile/*"]}]}
'@
$secret = @'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"secretsmanager:GetSecretValue",
 "Resource":"arn:aws:secretsmanager:__REGION__:__ACCOUNT__:secret:tripplanner/llm-api-key*"}]}
'@
$bedrock = $bedrock.Replace("__REGION__", $REGION).Replace("__ACCOUNT__", $ACCOUNT_ID)
$secret = $secret.Replace("__REGION__", $REGION).Replace("__ACCOUNT__", $ACCOUNT_ID)
foreach ($pat in @("Flights", "Weather", "Orchestra")) {
  $r = (aws iam list-roles --query "Roles[?contains(RoleName,'AgentCore-tripplannera2a') && contains(RoleName,'$pat')].RoleName | [0]" --output text).Trim()
  if (-not $r -or $r -eq "None") { Write-Host "AVISO: no encontre el rol de '$pat'; omito sus permisos de modelo." -ForegroundColor Yellow; continue }
  Apply-Policy "InvokeBedrockModels" $bedrock $r
  Apply-Policy "ReadLlmApiKey" $secret $r
}

Write-Host ""
Write-Host "Esperando 20 segundos a que IAM propague los permisos..."
Start-Sleep -Seconds 20
Write-Host "Listo. Permisos de especialistas, memoria, Lambdas y modelo aplicados." -ForegroundColor Green
