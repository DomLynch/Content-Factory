# Guard for any GPU job. Usage: powershell -File gpu_guard.ps1 -Match "flux_tile_test" [-MinFreeRamGB 4] [-MaxGpuGB 30]
# Kills the matching python job and writes the reason to Documents\crash_monitor.csv if free RAM or GPU dedicated memory crosses the limit
# (3 samples in a row, 2 s apart). Set -MaxGpuGB 0 to disable the GPU limit.
param([string]$Match, [double]$MinFreeRamGB = 4, [double]$MaxGpuGB = 30)
$log = "$env:USERPROFILE\Documents\crash_monitor.csv"
$strikes = 0; $gone = 0
"$(Get-Date -Format s),GUARD,armed on '$Match' min free RAM $MinFreeRamGB GB" | Out-File $log -Append -Encoding ascii
while ($true) {
  # A single empty CIM result must not end the guard (it exited early once while the job was still running): require 5 empty reads in a row.
  $procs = Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue } | Where-Object { $_.CommandLine -like "*$Match*" }
  if (-not $procs) {
    $gone++
    if ($gone -ge 5) { "$(Get-Date -Format s),GUARD,job '$Match' finished, guard exiting" | Out-File $log -Append -Encoding ascii; break }
    Start-Sleep 2; continue
  }
  $gone = 0
  $free = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
  $gpu = 0
  try { $gpu = (((Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples | Measure-Object CookedValue -Sum).Sum) / 1GB } catch {}
  $bad = ($free -lt $MinFreeRamGB) -or ($MaxGpuGB -gt 0 -and $gpu -gt $MaxGpuGB)
  if ($bad) { $strikes++ } else { $strikes = 0 }
  if ($strikes -ge 2) {
    $why = "free RAM {0:N1} GB (min {1}) / GPU dedicated {2:N1} GB (max {3})" -f $free, $MinFreeRamGB, $gpu, $MaxGpuGB
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    "$(Get-Date -Format s),GUARD_ABORT,job '$Match' killed: $why" | Out-File $log -Append -Encoding ascii
    break
  }
  Start-Sleep 1
}
