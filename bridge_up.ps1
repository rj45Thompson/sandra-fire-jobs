# Put Muster's chat on the public web page, from this computer.
#
#   powershell -ExecutionPolicy Bypass -File bridge_up.ps1
#
# Starts the engine, opens a tunnel to it, and publishes the address to the
# public page itself. Nothing to copy, nothing to send back: open the page and
# it connects. Close this window and it goes back to saying the desk is down.
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
    Write-Host "cloudflared is not here yet - installing it..." -ForegroundColor Cyan
    $winget = (Get-Command winget -ErrorAction SilentlyContinue).Source
    if ($winget) {
        & $winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
        # winget does not refresh this process's PATH, so look again properly.
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
    }
    if (-not $cf) {
        # No winget, or it failed. Fetch the single binary instead - it needs
        # no installer and no admin rights.
        try {
            $dest = Join-Path $root "cloudflared.exe"
            Write-Host "Downloading cloudflared..." -ForegroundColor Cyan
            Invoke-WebRequest -UseBasicParsing -OutFile $dest `
              "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
            $cf = $dest
        } catch {
            Write-Host ""
            Write-Host "Could not install cloudflared automatically: $_" -ForegroundColor Yellow
            Write-Host "Install it by hand:  winget install --id Cloudflare.cloudflared"
            Write-Host "The engine is running locally in the meantime. Ctrl-C to stop."
            Wait-Process -Id $engine.Id
            exit 0
        }
    }
    Write-Host "cloudflared ready." -ForegroundColor Green
}

Write-Host "Opening the tunnel..." -ForegroundColor Cyan
Write-Host ""
Write-Host "The page will open by itself once the tunnel is up, pointed here." -ForegroundColor Cyan
Write-Host "The same link is saved as open-me.txt beside this script." -ForegroundColor Cyan
Write-Host "If GITHUB_TOKEN is set the address also publishes itself, and any" -ForegroundColor Cyan
Write-Host "already-open page picks it up within half a minute." -ForegroundColor Cyan
Write-Host ""

$log = Join-Path $root "tunnel.log"
if (Test-Path $log) { Remove-Item $log -Force }      # or we publish the last run's address

# The address changes every time a quick tunnel starts, which is why it kept
# having to be carried by hand. Watch for it and write it into relay.json on
# the site instead; the page re-reads that file on a timer and connects itself.
$page = $env:MUSTER_PAGE
if (-not $page) { $page = "https://rj45thompson.github.io/starfighter/muster/index.html" }
$publish = Join-Path $root "link\publish_address.py"
$watcher = $null
if (Test-Path $publish) {
    $watcher = Start-Job -ArgumentList $log, $py, $publish, $page -ScriptBlock {
        param($log, $py, $publish, $page)
        $seen = ""
        foreach ($i in 1..120) {
            Start-Sleep -Seconds 1
            if (-not (Test-Path $log)) { continue }
            $hit = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" |
                   Select-Object -First 1
            if (-not $hit) { continue }
            $addr = $hit.Matches.Value
            if ($addr -eq $seen) { continue }
            $seen = $addr
            $link = "$page#desk=" + [uri]::EscapeDataString("$addr/bridge/chat")

            # Write-Host inside a background job never reaches this console, so
            # the link goes somewhere it can actually be found: on disk beside
            # the log, and open in the browser. Waiting for the tunnel to close
            # before showing the address would be showing it too late.
            Set-Content -Path (Join-Path (Split-Path -Parent $log) "open-me.txt") `
                        -Value $link -Encoding UTF8
            try { Start-Process $link } catch { }
            "OPEN THIS - the page, pointed at this computer:"
            $link
            "Nothing to set up. Send it to anyone who should chat; they need the code too."

            & $py $publish $addr 2>&1
            break
        }
    }
} else {
    Write-Host "! link\publish_address.py is missing - the address will not publish itself." -ForegroundColor Yellow
}

try {
    # Tee it, so the address can be pulled back out and shown on its own.
    & $cf tunnel --url "http://127.0.0.1:$port" --no-autoupdate 2>&1 | Tee-Object -FilePath $log
} finally {
    $addr = (Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" `
             -ErrorAction SilentlyContinue | Select-Object -First 1).Matches.Value
    if ($watcher) {
        Receive-Job $watcher -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        Remove-Job $watcher -Force -ErrorAction SilentlyContinue
    }
    if ($addr) {
        Write-Host ""
        Write-Host "  This computer was reachable at:  $addr" -ForegroundColor Green
        Write-Host ""
    }
    # Say the page is off rather than leaving it pointed at an address that is
    # gone - otherwise it spends a minute failing before it gives up.
    if (Test-Path $publish) { & $py $publish 2>&1 | ForEach-Object { Write-Host $_ } }
    Write-Host "Stopping the engine." -ForegroundColor Cyan
    if (-not $engine.HasExited) { $engine.Kill() }
}
