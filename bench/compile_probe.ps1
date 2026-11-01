# torch.compile probe runner (plugin 0.1.23). Runs bench\compile_probe.py twice, eager then compiled, one process each, under gpu_guard, 5-minute hard timeout each,
# one NO-reference cell at the size of the chosen row (default gear_cuffs). Output only to <outputs_dir>\_speed_tests\ (never kits_out\vampire_gpt_sheet).
# Usage: powershell -NoProfile -File bench\compile_probe.ps1 -Kit <kit.json> [-Row gear_cuffs] [-Dry] [-SkipEager -EagerJson <existing eager result json>]
#   -Dry sets CF_DRY=1: prints the plan and exits before torch loads. The script installs NO package; a missing one lands in the result's `error`.
#   -SkipEager -EagerJson: run the compiled mode only, against an eager result that already exists (same row, same seed), without repeating the 90 s baseline.
# A step is judged by its result json (present, parseable, with a sha256), NOT by the process exit code: Start-Process -PassThru can report an empty ExitCode after a
# timed wait, which made Probe 3 stop after a good eager run. The exit code and the last stderr lines are logged into each json instead.
param([Parameter(Mandatory=$true)][string]$Kit, [string]$Row = "gear_cuffs", [switch]$Dry, [switch]$SkipEager, [string]$EagerJson = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content "$repo\config\config.json" -Raw | ConvertFrom-Json
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$py = $loc.python
if ($SkipEager -and (-not $EagerJson -or -not (Test-Path $EagerJson))) { Write-Error "-SkipEager needs -EagerJson <path of an existing eager result json>"; exit 2 }
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$outDir = "$($loc.outputs_dir)\_speed_tests"
New-Item -ItemType Directory -Force $outDir | Out-Null
$lockPath = "$env:USERPROFILE\Documents\.gpu_lock"
if (-not $Dry -and (Test-Path $lockPath)) {
  try { $l = Get-Content $lockPath -Raw | ConvertFrom-Json; if (Get-Process -Id $l.pid -ErrorAction SilentlyContinue) { Write-Error "GPU busy: lock held by pid $($l.pid) job $($l.job). Not starting."; exit 4 } } catch {}
}
$k = (& $py "$repo\bench\speed_tests.py" kit $Kit $Row probe $stamp --no-ref | Select-Object -Last 1) | ConvertFrom-Json
if ($Dry) { $env:CF_DRY = "1" } else { Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue }
$jsons = @{}
foreach ($mode in @("eager", "compiled")) {
  if ($mode -eq "eager" -and $SkipEager) { $jsons[$mode] = $EagerJson; Write-Host "[eager] skipped, using $EagerJson"; continue }
  $json = "$outDir\compile_probe_${stamp}_$mode.json"
  $log = "$outDir\compile_probe_${stamp}_$mode.log"
  $g = $null
  if (-not $Dry) {
    $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","compile_probe","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
  }
  $p = Start-Process $py -ArgumentList "-u","`"$repo\bench\compile_probe.py`"","`"$($k.kit)`"",$k.row_id,"--mode",$mode,"--out","`"$json`"" -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  $null = $p.Handle   # keep the process handle so ExitCode is readable after the timed wait
  if (-not $p.WaitForExit(300000)) { & taskkill /PID $p.Id /T /F | Out-Null; Write-Warning "[$mode] hard timeout (5 min), killed" } else { $p.WaitForExit() }
  if ($g -and -not $g.HasExited) { Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
  $code = $p.ExitCode
  Write-Host "[$mode] exit code '$code'"; Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 12 | Out-Host
  $jsons[$mode] = $json
  if ($Dry) { continue }
  $valid = $false
  if (Test-Path $json) {
    try {
      $o = Get-Content $json -Raw | ConvertFrom-Json
      $tail = (Get-Content "$log.err" -ErrorAction SilentlyContinue | Select-Object -Last 8) -join "`n"
      $o | Add-Member -NotePropertyName runner_exit_code -NotePropertyValue "$code" -Force
      $o | Add-Member -NotePropertyName runner_stderr_tail -NotePropertyValue $tail -Force
      $o | ConvertTo-Json -Depth 8 | Out-File $json -Encoding utf8
      $valid = [bool]$o.sha256
    } catch { $valid = $false }
  }
  if ($mode -eq "eager" -and -not $valid) { Write-Error "eager baseline produced no valid result json ($json); exit code '$code', see $log.err"; exit 6 }
  if ($mode -eq "compiled" -and -not $valid) { Write-Warning "compiled run produced no valid result json (fell back or failed); exit code '$code', see $log.err" }
}
Remove-Item Env:CF_DRY -ErrorAction SilentlyContinue
if ($Dry) { Write-Host "DRY RUN OK: nothing rendered. Result json would be $outDir\compile_probe_$stamp.json"; exit 0 }
if (-not (Test-Path $jsons["compiled"])) { '{"compiled": false, "fell_back": true, "error": "no compiled result json written"}' | Out-File $jsons["compiled"] -Encoding utf8 }
& $py "$repo\bench\speed_tests.py" probe $jsons["eager"] $jsons["compiled"] "$outDir\compile_probe_$stamp.json"
Write-Host "RESULT $outDir\compile_probe_$stamp.json"
