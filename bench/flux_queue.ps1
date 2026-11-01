# Usage: flux_queue.ps1 <kit.json> [-MinStartGB 12] [-Retries 3] [-IdentityWaitMaxS <seconds>]
# Detached Flux queue: one row per process under the guard; waits for free RAM before each row, retries a killed row.
# Every decision comes from the kit and config\config.json (queue block) through tools\queue_plan.py, none from this script:
#   order      the kit's own row order (identity cells first, 3D-feeding next, close-ups last: tools\make_kit.py emits it)
#   skips      hold file (queue.hold_file, default <outputs_dir>\_queue\<kit>_hold.txt), mirror_of rows, rows whose png exists
#              (a swatch_of row is skipped only when its crop png exists, else it renders), re-roll cap per asset_id
#              (prompt + seed + reference sha: two ok renders of the same asset_id and the row is skipped)
#   reference  a row that is not an identity cell (kit key identity: true) waits until manifests\<kit>.json rows[<identity>].verdict is PASS;
#   first      polls every queue.identity_poll_s up to queue.identity_wait_max_s, logs HOLD, then moves on (re-checked on the next pass)
param([string]$Kit, [double]$MinStartGB = 12, [int]$Retries = 3, [int]$IdentityWaitMaxS = -1)
$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content "$repo\config\config.json" -Raw | ConvertFrom-Json
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$k = Get-Content $Kit -Raw | ConvertFrom-Json
$plan = "$repo\tools\queue_plan.py"
$state = "$($loc.work_dir)\flux_queue_$($k.kit).log"
function Log($m){ "$(Get-Date -Format s) $m" | Out-File $state -Append -Encoding utf8 }
function FreeGB { [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1) }
function Plan([string[]]$a){ ((& $loc.python $plan @a --repo $repo) -join "`n") | ConvertFrom-Json }
$info = Plan @("info", $Kit)
if(-not $info){ Log "ABORT queue_plan.py info failed for $Kit"; exit 1 }
$maxWait = if($IdentityWaitMaxS -ge 0){ $IdentityWaitMaxS } else { [int]$info.identity_wait_max_s }
$poll = [Math]::Max(1, [int]$info.identity_poll_s)
Log "QUEUE $($info.kit) rows=$(@($info.rows).Count) identity=$(@($info.identity) -join ',') hold_file=$($info.hold_file) wait_max=${maxWait}s poll=${poll}s reroll_cap=$($info.reroll_cap)"
$logged = @{}   # log a skip or HOLD reason once per row and reason, not once per sweep
function LogOnce($key, $m){ if(-not $logged.ContainsKey($key)){ $logged[$key] = 1; Log $m } }
for($pass=0; $pass -lt $Retries; $pass++){
  $waited = 0
  do {
    $rendered = 0; $waiting = @(); $waitFor = @()
    foreach($ir in $info.rows){
      $rowId = $ir.row_id
      $d = Plan @("decide", $Kit, $rowId)
      if(-not $d){ Log "SKIP $rowId queue_plan.py decide failed"; continue }
      if($d.action -eq "skip"){
        if($d.reason -like "REROLL_CAP*"){ LogOnce "cap|${rowId}|$($d.asset_id)" "REROLL_CAP $rowId $($d.reason)" }
        elseif($d.reason -eq "hold file"){ LogOnce "hold|$rowId" "HOLD $rowId listed in $($info.hold_file)" }
        continue
      }
      if($d.action -eq "wait"){ $waiting += $rowId; $waitFor += @($d.wait_for); LogOnce "wait|$rowId" "HOLD $rowId $($d.reason)"; continue }
      # settle: wait until no flux_kit python is left and RAM is back above the gate, poll every 2 s, cap 25 s
      for($s=0; $s -lt 13; $s++){ $left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -match 'flux_kit' }).Count; if($left -eq 0 -and (FreeGB) -ge $MinStartGB){ break }; Start-Sleep 2 }
      $w=0; while((FreeGB) -lt $MinStartGB -and $w -lt 360){ Start-Sleep 5; $w++ }   # up to 30 min
      if((FreeGB) -lt $MinStartGB){ Log "SKIP $rowId low RAM $(FreeGB) GB"; continue }
      $g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","flux_kit","-MinFreeRamGB",$cfg.limits.guard_min_free_ram_gb,"-MaxGpuGB",$cfg.limits.guard_max_gpu_gb -PassThru -WindowStyle Hidden
      $log = "$($loc.logs_dir)\kit_$($k.kit)_$($ir.name).txt"
      Log "START $rowId asset_id=$($d.asset_id) free=$(FreeGB)"
      & $loc.python -u "$repo\flux\flux_kit.py" $Kit $rowId *>&1 | Out-File $log -Append -Encoding utf8
      if(-not $g.HasExited){ Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
      $rc = $LASTEXITCODE
      if($rc -eq 2 -and (Select-String -Path $log -Pattern "SHEET RULE" -Quiet -ErrorAction SilentlyContinue)){ Log "HOLD $rowId reference-first gate: identity cell has no PASS verdict yet; not a failed cell, re-checked on the next pass"; $waiting += $rowId; continue }
      $rendered++
      $ok = [bool](Get-ChildItem "$($loc.outputs_dir)\$($k.kit)\$($ir.name)_*.png" -ErrorAction SilentlyContinue)
      $tail = (Get-Content $log -Tail 3 -ErrorAction SilentlyContinue | % { $_.Trim() } | ? { $_ } ) -join " | "
      Log "END $rowId ok=$ok"
      if(-not $ok){ Log "  FAIL_TAIL $rowId :: $tail" }
    }
    # Nothing rendered and rows are waiting for an identity cell: poll for its PASS verdict, then sweep again.
    $again = $false
    if($waiting.Count -gt 0 -and $rendered -eq 0){
      if($waited -lt $maxWait){
        Log "HOLD $($waiting.Count) row(s) wait for identity cell(s) $((@($waitFor) | Sort-Object -Unique) -join ',') to reach verdict PASS; poll ${poll}s, waited ${waited}s of ${maxWait}s"
        Start-Sleep $poll; $waited += $poll; $again = $true
      } else {
        Log "HOLD gave up after ${waited}s: $($waiting.Count) row(s) still wait for identity cell(s) $((@($waitFor) | Sort-Object -Unique) -join ','); next pass re-checks"
      }
    }
  } while($again)
}
Log "DONE"
