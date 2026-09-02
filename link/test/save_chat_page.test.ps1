<#
  The copy of the chat page that makes this machine self-sufficient.

  Everything here is about the two ways keeping a cache goes wrong: losing a
  working copy to a failed refresh, and leaving a half-written file where the
  real one should be. The network is handed in, so none of this touches it.
#>
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot ".." "save_chat_page.ps1")

$pass = 0; $fail = 0
function ok($name, $cond, $extra) {
    if ($cond) { $script:pass++; Write-Host "  ok   $name" }
    else { $script:fail++; Write-Host "  FAIL $name $(if($extra){"<$extra>"})" -ForegroundColor Red }
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("chatpage-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
$chat = Join-Path $work "muster.html"
$BIG  = "x" * 40000
$GOOD = "the copy that already worked" + ("y" * 40000)

Write-Host "`na page that downloads"
$r = Save-ChatPage -Url "https://example/page" -Path $chat -Fetch {
    param($u, $o) Set-Content -Path $o -Value $BIG -NoNewline }
ok "it says it refreshed" ($r.Ok -and $r.Refreshed) "$($r.Ok)/$($r.Refreshed)"
ok "the page is on disk" (Test-Path $chat)
ok "nothing half-written is left behind" (-not (Test-Path "$chat.new"))

Write-Host "`nthe fetch fails, and there is already a good copy"
Set-Content -Path $chat -Value $GOOD -NoNewline
$r = Save-ChatPage -Url "https://example/page" -Path $chat -Fetch {
    param($u, $o) throw "the network is not there" }
ok "it still reports a usable page" $r.Ok
ok "but does not claim to have refreshed it" (-not $r.Refreshed)
ok "it says why" ($r.Reason -match "not there") $r.Reason
ok "the good copy is untouched" ((Get-Content $chat -Raw) -eq $GOOD)
ok "no leftover .new" (-not (Test-Path "$chat.new"))

Write-Host "`nan error page comes back instead of the real one"
# This is the one that matters: a captive portal, a 404 body, a redirect stub.
# All of them download perfectly well and would overwrite a working chat.
$r = Save-ChatPage -Url "https://example/page" -Path $chat -Fetch {
    param($u, $o) Set-Content -Path $o -Value "<html>404</html>" -NoNewline }
ok "it refuses to call that a refresh" (-not $r.Refreshed)
ok "the good copy survives" ((Get-Content $chat -Raw) -eq $GOOD)
ok "and the short file is cleaned up" (-not (Test-Path "$chat.new"))

Write-Host "`nthe fetch fails and there is no copy at all"
Remove-Item -Force $chat
$r = Save-ChatPage -Url "https://example/page" -Path $chat -Fetch {
    param($u, $o) throw "still nothing" }
ok "it says plainly that there is no page" (-not $r.Ok)
ok "and leaves nothing behind" (-not (Test-Path "$chat.new"))

Remove-Item -Recurse -Force $work
Write-Host "`n$pass passed, $fail failed"
if ($fail) { exit 1 }
