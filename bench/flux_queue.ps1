# Usage: flux_queue.ps1 <kit.json> [-MinStartGB 10] [-Retries 3]
# Detached Flux queue: one row per process under the guard; waits for free RAM before each row, retries a killed row, skips rows whose png exists.
param([string]$Kit, [double]$MinStartGB = 10, [int]$Retries = 3)
$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content "$repo\config\config.json" -Raw | ConvertFrom-Json
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$k = Get-Content $Kit -Raw | ConvertFrom-Json
$state = "$($loc.work_dir)\flux_queue_$($k.kit).log"
function Log($m){ "$(Get-Date -Format s) $m" | Out-File $state -Append -Encoding utf8 }
function FreeGB { [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1) }
for($pass=0; $pass -lt $Retries; $pass++){
  foreach($r in $k.rows){
    $rowId = if($r.row_id){ $r.row_id } else { $r.name }
    if(Get-ChildItem "$($loc.outputs_dir)\$($k.kit)\$($r.name)_*.png" -ErrorAction SilentlyContinue){ continue }
    Start-Sleep 25   # let the previous process release its memory before the RAM check
    $w=0; while((FreeGB) -lt $MinStartGB -and $w -lt 360){ Start-Sleep 5; $w++ }   # up to 30 min
    if((FreeGB) -lt $MinStartGB){ Log "SKIP $rowId low RAM $(FreeGB) GB"; continue }
    $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","flux_kit","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
    $log = "$($loc.logs_dir)\kit_$($k.kit)_$($r.name).txt"
    Log "START $rowId free=$(FreeGB)"
    & $loc.python -u "$repo\flux\flux_kit.py" $Kit $rowId *>&1 | Out-File $log -Encoding utf8
    if(-not $g.HasExited){ Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
    $ok = [bool](Get-ChildItem "$($loc.outputs_dir)\$($k.kit)\$($r.name)_*.png" -ErrorAction SilentlyContinue)
    Log "END $rowId ok=$ok"
  }
}
Log "DONE"





