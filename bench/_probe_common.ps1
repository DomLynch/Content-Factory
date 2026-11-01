# Shared by the Flux speed probes (plugin 0.1.23). Dot-source it: . "$PSScriptRoot\_probe_common.ps1"
# Invoke-Engine runs bench\flux_multi.py with a plan under gpu_guard and a hard kill; -Dry sets CF_DRY=1 (prints the expanded plan, no torch, no GPU, no guard).
$script:repo = Split-Path -Parent $PSScriptRoot
$script:cfg = Get-Content "$script:repo\config\config.json" -Raw | ConvertFrom-Json
$script:loc = Get-Content "$script:repo\config\config.local.json" -Raw | ConvertFrom-Json
$script:py = $script:loc.python
$script:outDir = "$($script:loc.outputs_dir)\_speed_tests"
New-Item -ItemType Directory -Force $script:outDir | Out-Null

function Assert-GpuFree {
  $lockPath = "$env:USERPROFILE\Documents\.gpu_lock"
  if (Test-Path $lockPath) {
    try { $l = Get-Content $lockPath -Raw | ConvertFrom-Json; if (Get-Process -Id $l.pid -ErrorAction SilentlyContinue) { Write-Error "GPU busy: lock held by pid $($l.pid) job $($l.job). Not starting."; exit 4 } } catch {}
  }
}

function Invoke-Engine([string]$Kit, [object[]]$Plan, [string]$Name, [int]$TimeoutSec, [switch]$Dry) {
  $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
  $result = "$script:outDir\${Name}_$stamp.json"
  $planFile = "$script:outDir\${Name}_${stamp}_plan.json"
  ConvertTo-Json -InputObject @($Plan) -Depth 5 | Out-File $planFile -Encoding utf8
  $log = "$script:outDir\${Name}_$stamp.log"
  if ($Dry) { $env:CF_DRY = "1" } else { Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue }
  $g = $null
  if (-not $Dry) {
    $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$script:loc.guard_script,"-Match","flux_multi","-MinFreeRamGB",$script:cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$script:cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
  }
  $p = Start-Process $script:py -ArgumentList "-u","`"$script:repo\bench\flux_multi.py`"","`"$Kit`"","--plan-file","`"$planFile`"","--out","`"$result`"","--timeout-s",$TimeoutSec -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  if (-not $p.WaitForExit(($TimeoutSec + 30) * 1000)) { & taskkill /PID $p.Id /T /F | Out-Null; Write-Warning "[$Name] hard timeout ($TimeoutSec s), killed; partial results are in $result"; }
  if ($g -and -not $g.HasExited) { Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
  Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue
  Get-Content $log | Select-Object -Last 40 | Out-Host   # Out-Host: the function must return ONLY the result path
  if ($Dry) { Write-Host "DRY RUN OK ($Name): nothing rendered. Result json would be $result"; return $null }
  if ($p.HasExited -and $p.ExitCode -ne 0) { Write-Warning "[$Name] exit $($p.ExitCode), see $log.err" }
  return $result
}
