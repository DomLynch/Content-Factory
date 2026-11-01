# Usage: run_kit.ps1 <kit.json>  -- one row per process, guard on each, one GPU job at a time. Paths and limits come from config/.
param([string]$Kit)
$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content "$repo\config\config.json" -Raw | ConvertFrom-Json
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$k = Get-Content $Kit -Raw | ConvertFrom-Json
foreach($r in $k.rows){
  $rowId = if($r.row_id){ $r.row_id } else { $r.name }
  if(Get-ChildItem "$($loc.outputs_dir)\$($k.kit)\$($r.name)_*.png" -ErrorAction SilentlyContinue){ continue }  # already rendered
  $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","flux_kit","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
  $log = "$($loc.logs_dir)\kit_$($k.kit)_$($r.name).txt"
  & $loc.python -u "$repo\flux\flux_kit.py" $Kit $rowId *>&1 | Out-File $log -Encoding utf8
  if(-not $g.HasExited){ Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
}
"DONE" | Out-File "$($loc.work_dir)\kit_$($k.kit)_done.txt"
