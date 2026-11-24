# cambiar-cuenta.ps1
# Prepara el proyecto para desplegar en la cuenta de AWS que este activa en esta terminal:
#   1) apunta agentcore\aws-targets.json a esa cuenta y aparta el estado de la cuenta anterior
#   2) vuelve a Bedrock (Nova Pro) como modelo de los agentes
#   3) prueba que Bedrock responde
#   4) lista lo que ya existe en la cuenta, por si hay restos de despliegues anteriores
# Antes: aws configure --profile vieja   y luego   $env:AWS_PROFILE = "vieja"
# Ejecutar en la TERMINAL, desde la raiz del proyecto:
#     powershell -ExecutionPolicy Bypass -File .\cambiar-cuenta.ps1

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$REGION = "us-east-1"
function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }
function Stop-Here($m) { Say ""; Say "ERROR: $m" "Red"; exit 1 }

$acc = "$(aws sts get-caller-identity --query Account --output text 2>$null)".Trim()
if ($acc -notmatch '^\d{12}$') { Stop-Here "No hay credenciales validas. Corre: aws configure --profile vieja ; luego: `$env:AWS_PROFILE = 'vieja'" }
$prof = $env:AWS_PROFILE; if (-not $prof) { $prof = "(por defecto)" }
Say "Cuenta activa: $acc   Perfil: $prof   Region: $REGION" "Cyan"

python set_model.py --sync-account $acc
if ($LASTEXITCODE -ne 0) { Stop-Here "No pude actualizar aws-targets.json." }
python set_model.py --ensure
python set_model.py --provider bedrock --model-id amazon.nova-pro-v1:0
if ($LASTEXITCODE -ne 0) { Stop-Here "No pude volver a Bedrock en agentcore.json." }

$msgFile = Join-Path ([System.IO.Path]::GetTempPath()) "tp_msgs.json"
[System.IO.File]::WriteAllText($msgFile, '[{"role":"user","content":[{"text":"Responde solo: ok"}]}]')
aws bedrock-runtime converse --model-id amazon.nova-pro-v1:0 --messages "file://$msgFile" --region $REGION --query "output.message.content[0].text" --output text 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  aws bedrock-runtime converse --model-id amazon.nova-pro-v1:0 --messages "file://$msgFile" --region $REGION --query "output.message.content[0].text" --output text
  Stop-Here "Bedrock no responde con esta cuenta por la API (arriba esta el detalle)."
}
Say "Bedrock Nova Pro: OK en la cuenta $acc" "Green"

Say ""
Say "Lo que ya existe en esta cuenta:" "Cyan"
$stacks = "$(aws cloudformation list-stacks --region $REGION --query "StackSummaries[?StackStatus!='DELETE_COMPLETE' && (starts_with(StackName,'AgentCore-') || starts_with(StackName,'tripplanner'))].[StackName,StackStatus]" --output text 2>$null)".Trim()
$tables = "$(aws dynamodb list-tables --region $REGION --query "TableNames[?starts_with(@,'TripPlanner')]" --output text 2>$null)".Trim()
$runs = "$(aws bedrock-agentcore-control list-agent-runtimes --region $REGION --query "agentRuntimes[?starts_with(agentRuntimeId,'tripplannera2a')].agentRuntimeId" --output text 2>$null)".Trim()
if ($stacks) { Say "  Stacks: $stacks" "Yellow" } else { Say "  Stacks: ninguno" }
if ($tables) { Say "  Tablas DynamoDB: $tables" "Yellow" } else { Say "  Tablas DynamoDB: ninguna" }
if ($runs) { Say "  Runtimes de AgentCore: $runs" "Yellow" } else { Say "  Runtimes de AgentCore: ninguno" }
if ($tables -and $stacks -notmatch "tripplanner-backend") {
  Say ""
  Say "ATENCION: hay tablas TripPlanner-* sin el stack tripplanner-backend. El paso 3 chocaria con ellas." "Red"
  Say "Mandame esta pantalla antes de seguir." "Red"
  exit 2
}

Say ""
Say "Listo. Ahora despliega con:" "Green"
Say "  powershell -ExecutionPolicy Bypass -File .\deploy-all.ps1" "Green"
