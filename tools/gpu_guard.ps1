# Guard for any GPU job. Usage: powershell -File gpu_guard.ps1 -Match "flux_tile_test" [-MinFreeRamGB 4] [-MaxGpuGB 30]
# Kills the matching python job and writes the reason to Documents\crash_monitor.csv if free RAM or GPU dedicated memory crosses the limit
# (3 samples in a row, 2 s apart). Set -MaxGpuGB 0 to disable the GPU limit.
# With the Variable Graphics Memory split the GPU job's own allocations are carved out of system RAM, so "available RAM" falls toward 0 during a healthy decode or bake
# (13:30 2026-11-02: a 1024 decode was killed at 0.0 GB free with 52 GB free before it). The guard therefore watches COMMIT headroom (-MinFreeCommitGB, default 2)
# and treats -MinFreeRamGB 0 as "no physical-RAM floor"; config limits.guard_min_free_ram_gb is 0 for that reason.
param([string]$Match, [double]$MinFreeRamGB = 4, [double]$MaxGpuGB = 30, [double]$MinFreeCommitGB = 2)
$log = "$env:USERPROFILE\Documents\crash_monitor.csv"
$strikes = 0; $gone = 0; $lockPath = "$env:USERPROFILE\Documents\.gpu_lock"; $haveLock = $false
# GPU lock (Bonsai and other background helpers yield while it exists). CPU-only Blender guards do not take it.
function Write-Lock($pid_) { if ($Match -eq "blender") { return }; $o = @{ pid = $pid_; job = $Match; started_utc = (Get-Date).ToUniversalTime().ToString("s") + "Z" } | ConvertTo-Json -Compress; [IO.File]::WriteAllText($lockPath, $o); $script:haveLock = $true }
function Remove-Lock { if ($script:haveLock -and (Test-Path $lockPath)) { Remove-Item $lockPath -Force -ErrorAction SilentlyContinue }; $script:haveLock = $false }
if (Test-Path $lockPath) { try { $old = Get-Content $lockPath -Raw | ConvertFrom-Json; if (-not (Get-Process -Id $old.pid -ErrorAction SilentlyContinue)) { Remove-Item $lockPath -Force } } catch { Remove-Item $lockPath -Force -ErrorAction SilentlyContinue } }
"$(Get-Date -Format s),GUARD,armed on '$Match' min free RAM $MinFreeRamGB GB" | Out-File $log -Append -Encoding ascii
while ($true) {
  # A single empty CIM result must not end the guard (it exited early once while the job was still running): require 5 empty reads in a row.
  # Blender jobs are blender.exe; everything else is a python.exe job. Never match the Blender MCP connector (its python.exe has "blender" in its path).
  $pname = if ($Match -eq "blender") { "blender.exe" } else { "python.exe" }
  $procs = Get-CimInstance Win32_Process -Filter "Name='$pname'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$Match*" }
  if (-not $procs) {
    $gone++
    if ($gone -ge 5) { "$(Get-Date -Format s),GUARD,job '$Match' finished, guard exiting" | Out-File $log -Append -Encoding ascii; Remove-Lock; break }
    Start-Sleep 2; continue
  }
  $gone = 0
  if (-not $haveLock) { $first = @($procs)[0]; Write-Lock $first.ProcessId }
  $os = Get-CimInstance Win32_OperatingSystem
  $free = $os.FreePhysicalMemory / 1MB
  $freeCommit = $os.FreeVirtualMemory / 1MB   # commit headroom (RAM + page file not yet promised)
  $gpu = 0
  try { $gpu = (((Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples | Measure-Object CookedValue -Sum).Sum) / 1GB } catch {}
  $bad = ($MinFreeRamGB -gt 0 -and $free -lt $MinFreeRamGB) -or ($MinFreeCommitGB -gt 0 -and $freeCommit -lt $MinFreeCommitGB) -or ($MaxGpuGB -gt 0 -and $gpu -gt $MaxGpuGB)
  if ($bad) { $strikes++ } else { $strikes = 0 }
  if ($strikes -ge 2) {
    $why = "free RAM {0:N1} GB (min {1}) / free commit {4:N1} GB (min {5}) / GPU dedicated {2:N1} GB (max {3})" -f $free, $MinFreeRamGB, $gpu, $MaxGpuGB, $freeCommit, $MinFreeCommitGB
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    "$(Get-Date -Format s),GUARD_ABORT,job '$Match' killed: $why" | Out-File $log -Append -Encoding ascii
    Remove-Lock
    break
  }
  Start-Sleep 1
}
