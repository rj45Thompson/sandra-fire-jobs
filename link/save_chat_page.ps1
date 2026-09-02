<#
  Keep a copy of the chat page on this machine, and say where to open it.

  A page on github.io reaching http://127.0.0.1 is the fragile way to run
  this, and Chrome 138+ made it worse: it holds that request, silently, with
  no error, until somebody grants a local-network permission. So the chat
  reported offline while the engine sat here running.

  Served by the engine itself the question never arises. Same origin as
  /bridge/chat means no CORS, no permission to grant, no mixed content, no
  access code, and no address to remember or go stale.

  The copy is refreshed from the published page each run so there is still one
  source of truth. Two things that copy must never do, which is most of what
  is written here:

    lose a good copy to a bad fetch - an error page or a redirect is small,
    the real page is tens of KB, so anything short is discarded and whatever
    worked last time is left alone;

    leave a half-written file behind - the download goes to .new and is only
    moved into place once it has been looked at.
#>

function Save-ChatPage {
    param(
        [Parameter(Mandatory)] [string] $Url,
        [Parameter(Mandatory)] [string] $Path,
        [int] $MinBytes = 10000,
        [scriptblock] $Fetch      # tests hand in their own; production downloads
    )

    $tmp = "$Path.new"
    if (-not $Fetch) {
        $Fetch = { param($u, $o) Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -OutFile $o $u }
    }

    try {
        & $Fetch $Url $tmp
        if ((Test-Path $tmp) -and (Get-Item $tmp).Length -ge $MinBytes) {
            Move-Item -Force $tmp $Path
            return [pscustomobject]@{ Ok = $true; Refreshed = $true; Reason = "" }
        }
        Remove-Item -Force $tmp -ErrorAction SilentlyContinue
        return [pscustomobject]@{
            Ok = (Test-Path $Path); Refreshed = $false
            Reason = "what came back was too short to be the page" }
    } catch {
        Remove-Item -Force $tmp -ErrorAction SilentlyContinue
        return [pscustomobject]@{
            Ok = (Test-Path $Path); Refreshed = $false
            Reason = $_.Exception.Message }
    }
}
