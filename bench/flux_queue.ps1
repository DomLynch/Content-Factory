# Usage: flux_queue.ps1 <kit.json> [-MinStartGB 12] [-Retries 3]
# Detached Flux queue: one row per process under the guard; waits for free RAM before each row, retries a killed row, skips rows whose png exists.
param([string]$Kit, [double]$MinStartGB = 12, [int]$Retries = 3)
$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content "$repo\config\config.json" -Raw | ConvertFrom-Json
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$k = Get-Content $Kit -Raw | ConvertFrom-Json
$state = "$($loc.work_dir)\flux_queue_$($k.kit).log"
function Log($m){ "$(Get-Date -Format s) $m" | Out-File $state -Append -Encoding utf8 }
function FreeGB { [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1) }
# PRIORITY: 3D-feeding cells first, close-ups (review aids, not 3D inputs) last; the rest keep kit order
$first = "body_underlayer_back","body_underlayer_three_quarter","head_profile","head_back","head_three_quarter","head_top","hand_back_views","foot_left","foot_right"
$last = "head_eyes_closeup","gear_belt_buckle_closeup","gear_collar_closeup","head_profile_right"
$rows = @(); foreach($n in $first){ $rows += $k.rows | ? { $_.name -eq $n } }; $rows += $k.rows | ? { $first -notcontains $_.name -and $last -notcontains $_.name }; foreach($n in $last){ $rows += $k.rows | ? { $_.name -eq $n } }
for($pass=0; $pass -lt $Retries; $pass++){
  foreach($r in $rows){
    $rowId = if($r.row_id){ $r.row_id } else { $r.name }
    $hold = @(Get-Content C:\Users\dom12\Documents\flux_env\queue_hold.txt -ErrorAction SilentlyContinue)   # rows held until their reference or prompt fix lands
    if($hold -contains $rowId){ continue }
    if($r.mirror_of){ continue }
    if(Get-ChildItem "$($loc.outputs_dir)\$($k.kit)\$($r.name)_*.png" -ErrorAction SilentlyContinue){ continue }
    $tries = @(Get-Content $state -ErrorAction SilentlyContinue | ? { $_ -match " END $rowId ok=True" }).Count
    if($tries -ge 2 -and -not $r.seed){ Log "REROLL_CAP $rowId succeeded $tries times already with no row seed; add a documented change (row seed or prompt fix) before another roll"; continue }
    # settle: wait until no flux_kit python is left and RAM is back above the gate, poll every 2 s, cap 25 s
    for($s=0; $s -lt 13; $s++){ $left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -match 'flux_kit' }).Count; if($left -eq 0 -and (FreeGB) -ge $MinStartGB){ break }; Start-Sleep 2 }
    $w=0; while((FreeGB) -lt $MinStartGB -and $w -lt 360){ Start-Sleep 5; $w++ }   # up to 30 min
    if((FreeGB) -lt $MinStartGB){ Log "SKIP $rowId low RAM $(FreeGB) GB"; continue }
    $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","flux_kit","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
    $log = "$($loc.logs_dir)\kit_$($k.kit)_$($r.name).txt"
    Log "START $rowId free=$(FreeGB)"
    & $loc.python -u "$repo\flux\flux_kit.py" $Kit $rowId *>&1 | Out-File $log -Append -Encoding utf8
    if(-not $g.HasExited){ Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
    $rc = $LASTEXITCODE
    if($rc -eq 2 -and (Select-String -Path $log -Pattern "SHEET RULE" -Quiet -ErrorAction SilentlyContinue)){ Log "HOLD $rowId reference-first gate: identity cell has no PASS verdict yet; not a failed cell, re-checked on the next pass"; continue }
    $ok = [bool](Get-ChildItem "$($loc.outputs_dir)\$($k.kit)\$($r.name)_*.png" -ErrorAction SilentlyContinue)
    $tail = (Get-Content $log -Tail 3 -ErrorAction SilentlyContinue | % { $_.Trim() } | ? { $_ } ) -join " | "
    Log "END $rowId ok=$ok"
    if(-not $ok){ Log "  FAIL_TAIL $rowId :: $tail" }
  }
}
Log "DONE"







