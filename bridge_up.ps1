# Put Muster's chat on the public web page, from this computer.
#
#   powershell -ExecutionPolicy Bypass -File bridge_up.ps1
#
# Starts the engine, opens a tunnel to it, and prints the address to send back.
# No API key anywhere: the model runs through the Claude Code CLI already
# signed in here, so it rides your own subscription.
#
# What is exposed is ONE endpoint, /bridge/chat, which takes chat text plus the
# access code and returns chat text. The profile, applications, mailbox and
# applier stay behind the home-network rule and are not reachable from outside.
# Close this window and the whole thing is gone.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".env")) { throw "No .env here. Copy config.example.env to .env first." }
$envText = Get-Content ".env" -Raw
foreach ($needed in @("BRIDGE_CODE", "CHAT_PROVIDER=claude-cli")) {
    if ($envText -notmatch [regex]::Escape($needed)) {
        Write-Host "! .env is missing $needed - the bridge will refuse to answer." -ForegroundColor Yellow
    }
}

$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "No Python on PATH." }

Write-Host "Starting the engine..." -ForegroundColor Cyan
$engine = Start-Process -FilePath $py -ArgumentList "backend\server.py" `
                        -PassThru -WindowStyle Minimized

# Wait for it to answer rather than guessing at a sleep.
$port = 8770
if ($envText -match "API_PORT=(\d+)") { $port = $Matches[1] }
$health = $null
foreach ($i in 1..30) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($health.ok) { break }
    } catch { $health = $null }
}
if (-not $health) { $engine.Kill(); throw "The engine did not come up on port $port." }
Write-Host "Engine is up (chat provider: $($health.chat))." -ForegroundColor Green
if ($health.chat -ne "claude-cli") {
    Write-Host "! Chat is on '$($health.chat)', not claude-cli. Set CHAT_PROVIDER=claude-cli in .env." -ForegroundColor Yellow
}

$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) {
    Write-Host ""
    Write-Host "cloudflared is not installed, so there is no tunnel yet." -ForegroundColor Yellow
    Write-Host "Install it once with:  winget install --id Cloudflare.cloudflared"
    Write-Host "The engine is running locally in the meantime. Ctrl-C to stop."
    Wait-Process -Id $engine.Id
    exit 0
}

Write-Host "Opening the tunnel..." -ForegroundColor Cyan
Write-Host ""
Write-Host "Watch for a line like  https://something.trycloudflare.com" -ForegroundColor Cyan
Write-Host "Send that address back and the public page starts using this computer." -ForegroundColor Cyan
Write-Host ""

try {
    & $cf tunnel --url "http://127.0.0.1:$port"
} finally {
    Write-Host "Stopping the engine." -ForegroundColor Cyan
    if (-not $engine.HasExited) { $engine.Kill() }
}
