# torch.compile probe runner (plugin 0.1.23). Runs bench\compile_probe.py twice, eager then compiled, one process each, under gpu_guard, 5-minute hard timeout each,
# one NO-reference cell at the size of the chosen row (default gear_cuffs). Output only to <outputs_dir>\_speed_tests\ (never kits_out\vampire_gpt_sheet).
# Usage: powershell -NoProfile -File bench\compile_probe.ps1 -Kit <kit.json> [-Row gear_cuffs] [-Dry]
#   -Dry sets CF_DRY=1: prints the plan and exits before torch loads. The script installs NO package; a missing one lands in the result's `error`.
param([Parameter(Mandatory=$true)][string]$Kit, [string]$Row = "gear_cuffs", [switch]$Dry)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content "$repo\config\config.json" -Raw | ConvertFrom-Json
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$py = $loc.python
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$outDir = "$($loc.outputs_dir)\_speed_tests"
New-Item -ItemType Directory -Force $outDir | Out-Null
$lockPath = "$env:USERPROFILE\Documents\.gpu_lock"
if (-not $Dry -and (Test-Path $lockPath)) {
  try { $l = Get-Content $lockPath -Raw | ConvertFrom-Json; if (Get-Process -Id $l.pid -ErrorAction SilentlyContinue) { Write-Error "GPU busy: lock held by pid $($l.pid) job $($l.job). Not starting."; exit 4 } } catch {}
}
$k = (& $py "$repo\bench\speed_tests.py" kit $Kit $Row probe $stamp --no-ref | Select-Object -Last 1) | ConvertFrom-Json
if ($Dry) { $env:CF_DRY = "1" } else { Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue }
foreach ($mode in @("eager", "compiled")) {
  $json = "$outDir\compile_probe_${stamp}_$mode.json"
  $log = "$outDir\compile_probe_${stamp}_$mode.log"
  $g = $null
  if (-not $Dry) {
    $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","compile_probe","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
  }
  $p = Start-Process $py -ArgumentList "-u","`"$repo\bench\compile_probe.py`"","`"$($k.kit)`"",$k.row_id,"--mode",$mode,"--out","`"$json`"" -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  if (-not $p.WaitForExit(300000)) { & taskkill /PID $p.Id /T /F | Out-Null; Write-Error "[$mode] hard timeout (5 min), killed"; exit 5 }
  if ($g -and -not $g.HasExited) { Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
  Write-Host "[$mode] exit $($p.ExitCode)"; Get-Content $log | Select-Object -Last 12
  if (-not $Dry -and $mode -eq "eager" -and $p.ExitCode -ne 0) { Write-Error "eager baseline failed, see $log.err"; exit 6 }
}
Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue
if ($Dry) { Write-Host "DRY RUN OK: nothing rendered. Result json would be $outDir\compile_probe_$stamp.json"; exit 0 }
& $py "$repo\bench\speed_tests.py" probe "$outDir\compile_probe_${stamp}_eager.json" "$outDir\compile_probe_${stamp}_compiled.json" "$outDir\compile_probe_$stamp.json"
Write-Host "RESULT $outDir\compile_probe_$stamp.json"
