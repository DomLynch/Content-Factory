# Guard for any GPU job. Usage: powershell -File gpu_guard.ps1 -Match "flux_tile_test" [-MinFreeRamGB 4] [-MaxGpuGB 30]
# Kills the matching python job and writes the reason to Documents\crash_monitor.csv if free RAM or GPU dedicated memory crosses the limit
# (3 samples in a row, 2 s apart). Set -MaxGpuGB 0 to disable the GPU limit.
param([string]$Match, [double]$MinFreeRamGB = 4, [double]$MaxGpuGB = 30)
$log = "$env:USERPROFILE\Documents\crash_monitor.csv"
$strikes = 0; $gone = 0; $lockPath = "$env:USERPROFILE\Documents\.gpu_lock"; $haveLock = $false
# GPU lock (Bonsai and other background helpers yield while it exists). CPU-only Blender guards do not take it.
function Write-Lock($pid_) { if ($Match -eq "blender") { return }; $o = @{ pid = $pid_; job = $Match; started_utc = (Get-Date).ToUniversalTime().ToString("s") + "Z" } | ConvertTo-Json -Compress; [IO.File]::WriteAllText($lockPath, $o); $script:haveLock = $true }
function Remove-Lock { if ($script:haveLock -and (Test-Path $lockPath)) { Remove-Item $lockPath -Force -ErrorAction SilentlyContinue }; $script:haveLock = $false }
if (Test-Path $lockPath) { try { $old = Get-Content $lockPath -Raw | ConvertFrom-Json; if (-not (Get-Process -Id $old.pid -ErrorAction SilentlyContinue)) { Remove-Item $lockPath -Force } } catch { Remove-Item $lockPath -Force -ErrorAction SilentlyContinue } }
"$(Get-Date -Format s),GUARD,armed on '$Match' min free RAM $MinFreeRamGB GB" | Out-File $log -Append -Encoding ascii
while ($true) {
  # A single empty CIM result must not end the guard (it exited early once while the job was still running): require 5 empty reads in a row.
  $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$Match*" }
  if (-not $procs) {
    $gone++
    if ($gone -ge 5) { "$(Get-Date -Format s),GUARD,job '$Match' finished, guard exiting" | Out-File $log -Append -Encoding ascii; Remove-Lock; break }
    Start-Sleep 2; continue
  }
  $gone = 0
  if (-not $haveLock) { $first = @($procs)[0]; Write-Lock $first.ProcessId }
  $free = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
  $gpu = 0
  try { $gpu = (((Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples | Measure-Object CookedValue -Sum).Sum) / 1GB } catch {}
  $bad = ($free -lt $MinFreeRamGB) -or ($MaxGpuGB -gt 0 -and $gpu -gt $MaxGpuGB)
  if ($bad) { $strikes++ } else { $strikes = 0 }
  if ($strikes -ge 2) {
    $why = "free RAM {0:N1} GB (min {1}) / GPU dedicated {2:N1} GB (max {3})" -f $free, $MinFreeRamGB, $gpu, $MaxGpuGB
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    "$(Get-Date -Format s),GUARD_ABORT,job '$Match' killed: $why" | Out-File $log -Append -Encoding ascii
    Remove-Lock
    break
  }
  Start-Sleep 1
}
