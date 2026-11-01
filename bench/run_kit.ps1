# Usage: run_kit.ps1 <kit.json>  -- one row per process, guard on each, one GPU job at a time
param([string]$Kit)
$d="$env:USERPROFILE\Documents"; $py="$d\Trellis-3-312\.venv\Scripts\python.exe"
$repo="$d\Content-Factory"; $k=Get-Content $Kit -Raw | ConvertFrom-Json
foreach($r in $k.rows){
  if(Get-ChildItem "$d\kits_out\$($k.kit)\$($r.name)_*.png" -ErrorAction SilentlyContinue){ continue }  # already rendered
  $g = Start-Process powershell -ArgumentList "-NoProfile","-File","$d\gpu_guard.ps1","-Match","flux_kit","-MinFreeRamGB","2","-MaxGpuGB","0" -PassThru -WindowStyle Hidden
  $log="$d\flux_env\kit_$($k.kit)_$($r.name).txt"
  & $py -u "$repo\flux\flux_kit.py" $Kit $r.name *>&1 | Out-File $log -Encoding utf8
  if(-not $g.HasExited){ Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
}
"DONE" | Out-File "$d\kit_$($k.kit)_done.txt"
