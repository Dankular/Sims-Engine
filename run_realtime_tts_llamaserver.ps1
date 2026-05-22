$ErrorActionPreference = "Stop"

# Kill existing llama-server
Get-Process -Name "llama-server" -ErrorAction SilentlyContinue | Stop-Process -Force

# Tune adjudicator token budget
$env:SIM_V2_ADJ_MAX_TOKENS = "260"

# Start llama-server in background
$llamaArgs = @(
  "-hf", "unsloth/Qwen3.5-0.8B-MTP-GGUF:UD-Q4_K_XL",
  "--alias", "qwen3.5-0.8b-mtp",
  "--host", "127.0.0.1",
  "--port", "8080",
  "--ctx-size", "4096",
  "--threads", "8",
  "--spec-type", "draft-mtp",
  "--spec-draft-n-max", "6",
  "--reasoning", "off",
  "--no-webui",
  "-np", "1",              # single parallel slot — concurrent requests queue cleanly on CPU
  "--kv-cache-dtype", "fp8" # halves KV cache RAM, no meaningful quality loss
)

$llamaProc = Start-Process -FilePath "llama-server" -ArgumentList $llamaArgs -PassThru -WindowStyle Hidden

# Wait for readiness
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
  try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/models" -TimeoutSec 2
    $ready = $true
    break
  } catch {
    Start-Sleep -Seconds 1
  }
}

if (-not $ready) {
  Write-Error "llama-server did not become ready on 127.0.0.1:8080"
  if ($llamaProc -and -not $llamaProc.HasExited) {
    Stop-Process -Id $llamaProc.Id -Force
  }
  exit 1
}

Write-Host "[OK] llama-server ready. Starting simulation..."

# Launch sim (realtime fixed cadence)
python __main__.py `
  --backend llama-server `
  --llama-url http://127.0.0.1:8080/v1/chat/completions `
  --llama-model qwen3.5-0.8b-mtp `
  --sims 4 `
  --story `
  --realtime `
  --fps 20 `
  --sim-speed 3600 `
  --tts-device cpu
