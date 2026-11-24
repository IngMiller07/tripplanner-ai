# deploy-frontend.ps1
# Construye el frontend, escribe config.js con las URLs reales del stack y lo sube a S3.
# Ejecutar en la TERMINAL, desde la carpeta frontend (necesita Node.js y AWS CLI):
#     powershell -ExecutionPolicy Bypass -File .\deploy-frontend.ps1
# No hay credenciales en el frontend: config.js solo tiene dos URLs publicas.

$ErrorActionPreference = "Continue"
$STACK = "tripplanner-backend"
$REGION = "us-east-1"

function StackOut($key) {
  $v = "$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue | [0]" --output text 2>$null)".Trim()
  if ($v -eq "None") { return "" }
  return $v
}

function Stop-Here($msg) {
  Write-Host "ERROR: $msg" -ForegroundColor Red
  exit 1
}

$api = StackOut "ApiUrl"
$stream = StackOut "StreamUrl"
$bucket = StackOut "FrontendBucketName"
$site = StackOut "WebsiteUrl"
if (-not $api) { Stop-Here "No encontre ApiUrl. Desplegaste el stack tripplanner-backend?" }
if (-not $stream) { Stop-Here "No hay StreamUrl. Despliega el backend con el ARN del Orchestrator (paso 5 de deploy-all.ps1)." }
if (-not $bucket) { Stop-Here "No encontre el bucket del frontend en el stack." }
Write-Host "API:     $api"
Write-Host "STREAM:  $stream"
Write-Host "BUCKET:  $bucket"

if (-not (Test-Path "node_modules")) {
  npm install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { Stop-Here "npm install fallo" }
}
npm run build
if ($LASTEXITCODE -ne 0) { Stop-Here "npm run build fallo" }

$cfg = "window.TRIPPLANNER_CONFIG = { API_URL: `"$($api.TrimEnd('/'))`", STREAM_URL: `"$stream`" };"
[System.IO.File]::WriteAllText((Join-Path (Join-Path (Get-Location) "dist") "config.js"), $cfg)

aws s3 sync dist "s3://$bucket" --delete --region $REGION --cache-control "no-cache"
if ($LASTEXITCODE -ne 0) { Stop-Here "aws s3 sync fallo" }

Write-Host ""
Write-Host "Listo. Abre: $site" -ForegroundColor Green
exit 0
