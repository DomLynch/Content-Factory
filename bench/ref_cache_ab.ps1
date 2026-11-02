# Reference-cache A/B (plugin 0.1.23). Renders one row twice with the same seed, CF_REF_CACHE off then on, one row per process, under gpu_guard,
# 6-minute hard timeout per render, output only to <outputs_dir>\_speed_tests\ (never kits_out\vampire_gpt_sheet), result json next to the PNGs.
# Usage: powershell -NoProfile -File bench\ref_cache_ab.ps1 -Kit <kit.json> [-Row head_front] [-Dry]
#   -Dry sets CF_DRY=1: prints the throw-away kit and PNG paths and exits before any model loads (no GPU, no result json).
# Refuses to start when a GPU lock held by a live process exists.
param([Parameter(Mandatory=$true)][string]$Kit, [string]$Row = "head_front", [switch]$Dry)
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
  $busy = $null
  try { $l = Get-Content $lockPath -Raw | ConvertFrom-Json; if (Get-Process -Id $l.pid -ErrorAction SilentlyContinue) { $busy = "lock held by pid $($l.pid) job $($l.job)" } } catch {}
  if ($busy) { Write-Host "GPU busy: $busy. Not starting."; exit 4 }
}
$receipts = @{}
foreach ($variant in @("off", "on")) {
  $k = (& $py "$repo\bench\speed_tests.py" kit $Kit $Row $variant $stamp | Select-Object -Last 1) | ConvertFrom-Json
  Write-Host "[$variant] kit $($k.kit)  row $($k.row_id)  png $($k.png)"
  if ($Dry) { $env:CF_DRY = "1" } else { Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue }
  if ($variant -eq "on") { $env:CF_REF_CACHE = "1" } else { Remove-Item Env:CF_REF_CACHE -ErrorAction SilentlyContinue }
  $log = "$outDir\ref_cache_ab_${stamp}_$variant.log"
  $g = $null
  if (-not $Dry) {
    $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","flux_kit","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
  }
  $p = Start-Process $py -ArgumentList "-u","`"$repo\flux\flux_kit.py`"","`"$($k.kit)`"",$k.row_id -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  $null = $p.Handle   # keep the handle so ExitCode is readable after the timed wait
  if (-not $p.WaitForExit(360000)) { & taskkill /PID $p.Id /T /F | Out-Null; Write-Error "[$variant] hard timeout (6 min), killed"; exit 5 } else { $p.WaitForExit() }
  if ($g -and -not $g.HasExited) { Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
  Get-Content $log | Select-Object -Last 4
  if ($Dry) { continue }
  $m = Select-String -Path $log -Pattern "receipt=(\S+)" | Select-Object -Last 1   # judged by the RESULT line, not the exit code (an empty ExitCode after a timed wait stopped Probe 3)
  Write-Host "[$variant] exit code '$($p.ExitCode)'"
  if (-not $m) { Write-Error "[$variant] flux_kit.py printed no RESULT receipt (exit code '$($p.ExitCode)'), see $log and $log.err"; exit 6 }
  $receipts[$variant] = $m.Matches[0].Groups[1].Value
}
Remove-Item Env:CF_DRY, Env:CF_REF_CACHE -ErrorAction SilentlyContinue
if ($Dry) { Write-Host "DRY RUN OK: nothing rendered. Result json would be $outDir\ref_cache_ab_$stamp.json"; exit 0 }
& $py "$repo\bench\speed_tests.py" ab $receipts["off"] $receipts["on"] "$outDir\ref_cache_ab_$stamp.json"
Write-Host "RESULT $outDir\ref_cache_ab_$stamp.json"
