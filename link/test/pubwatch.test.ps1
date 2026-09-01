# The watcher inside bridge_up.ps1, run for real: does it find the address in a
# tunnel log and hand it to the publisher exactly once?
$ErrorActionPreference = "Stop"
$pass = 0; $fail = 0
function ok($name, $cond, $extra) {
  if ($cond) { $script:pass++; Write-Host "  ok   $name" }
  else { $script:fail++; Write-Host "  FAIL $name  $extra" }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("pubwatch-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
$log  = Join-Path $tmp "tunnel.log"
$seen = Join-Path $tmp "called.txt"

# Stand in for python: record the arguments it was handed.
$py = Join-Path $tmp "fakepy.sh"
@"
#!/bin/sh
echo "`$@" >> "$seen"
"@ | Set-Content -Path $py -NoNewline
chmod +x $py

# Lift the watcher out of the launcher so the code under test is the shipped code.
# The launcher sits at the root of the bridge, or beside the link folder in
# the copy that ships with the page. Take whichever is here.
$link = Split-Path -Parent $PSScriptRoot
$launcher = @((Join-Path $link "bridge_up.ps1"),
              (Join-Path (Split-Path -Parent $link) "bridge_up.ps1")) |
            Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $launcher) { throw "cannot find bridge_up.ps1 from $PSScriptRoot" }
$src = Get-Content $launcher -Raw
$m = [regex]::Match($src, '(?s)\$watcher = Start-Job -ArgumentList \$log, \$py, \$publish -ScriptBlock \{\s*(.*?)\n    \}\n\}')
ok "found the watcher in the launcher" $m.Success ""
if (-not $m.Success) { exit 1 }
$body = [scriptblock]::Create($m.Groups[1].Value)

Write-Host "`nan address that shows up after a moment"
"Thank you for trying Cloudflare Tunnel." | Set-Content $log
$job = Start-Job -ScriptBlock $body -ArgumentList $log, $py, "/publish.py"
Start-Sleep -Seconds 2
"|  https://sunny-moose-42.trycloudflare.com                    |" | Add-Content $log
Wait-Job $job -Timeout 20 | Out-Null
Receive-Job $job | Out-Null; Remove-Job $job -Force

ok "it called the publisher" (Test-Path $seen) "nothing was recorded"
$calls = if (Test-Path $seen) { @(Get-Content $seen) } else { @() }
ok "exactly once" ($calls.Count -eq 1) "got $($calls.Count)"
ok "with the address it found" ($calls -join "") -match "https://sunny-moose-42\.trycloudflare\.com" "got '$($calls -join '')'"
ok "and the script path" ($calls -join "") -match "/publish\.py" ""

Write-Host "`na log that never gets an address"
Remove-Item $seen -ErrorAction SilentlyContinue
"no address here" | Set-Content $log
$job = Start-Job -ScriptBlock $body -ArgumentList $log, $py, "/publish.py"
Start-Sleep -Seconds 4
Stop-Job $job; Remove-Job $job -Force
ok "it publishes nothing" (-not (Test-Path $seen)) "it published something anyway"

Remove-Item -Recurse -Force $tmp
Write-Host "`n$pass passed, $fail failed"
if ($fail) { exit 1 }
