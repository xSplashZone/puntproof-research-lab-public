param([int]$Port=8787, [switch]$NoBrowser, [switch]$BrowserTab)
$ErrorActionPreference='Stop'
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$python=Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$server=Join-Path $PSScriptRoot 'server.py'
$url="http://127.0.0.1:$Port"
$running=$false
try {
 $health=Invoke-RestMethod -Uri "$url/health" -TimeoutSec 2
 if($health.application -ne 'PuntProof Research Lab'){throw 'Port belongs to another application'}
 $running=$true
} catch {
 if($_.Exception.Message -eq 'Port belongs to another application'){throw}
}
if(-not $running){
 if(-not (Test-Path -LiteralPath $python)){throw 'Frozen project environment is missing. Follow RUNBOOK.md; do not use global Python.'}
 $logDir=Join-Path $repoRoot 'data\private\research-lab\logs'
 New-Item -ItemType Directory -Force -Path $logDir | Out-Null
 Start-Process -FilePath $python -ArgumentList @(('"'+$server+'"'),'--port',"$Port") -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'server.out.log') -RedirectStandardError (Join-Path $logDir 'server.err.log')
 for($i=0;$i -lt 40;$i++){
  Start-Sleep -Milliseconds 500
  try{$health=Invoke-RestMethod -Uri "$url/health" -TimeoutSec 1;if($health.application -eq 'PuntProof Research Lab'){$running=$true;break}}catch{}
 }
 if(-not $running){throw "Lab did not become healthy. Inspect $logDir\server.err.log"}
}
if(-not $NoBrowser){
 if($BrowserTab){Start-Process $url}
 else {
  $edgePaths=@("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe","$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
  $edge=$edgePaths | Where-Object {Test-Path -LiteralPath $_} | Select-Object -First 1
  if(-not $edge){throw 'Windowed launch requires Microsoft Edge. Use -BrowserTab to open the default browser.'}
  Start-Process -FilePath $edge -ArgumentList @("--app=$url","--new-window")
 }
}
Write-Output "Research Lab: $url | scientific execution requires separate admission"
