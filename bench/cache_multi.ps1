# Multi-cell reference-cache run (plugin 0.1.23): the same three REFERENCED rows rendered in one process twice (two processes in total): CF_REF_CACHE off, then on.
# Reports per row hits/misses (on), gen_s and wall_s for both, the speedup, the pixel diff between off and on, and the gen_s of the queue's own flux_kit.py receipt for the
# same row (read from the Factory checkout's receipts, read only). Output only to <outputs_dir>\_speed_tests\.
# Usage: powershell -NoProfile -File bench\cache_multi.ps1 -Kit <kit.json> [-Rows head_front,head_back,head_three_quarter] [-Receipts <Content-Factory\receipts>] [-TimeoutSec 900] [-Dry]
param([Parameter(Mandatory=$true)][string]$Kit, [string[]]$Rows = @("head_front","head_back","head_three_quarter"),
      [string]$Receipts = "C:\Users\dom12\Documents\Content-Factory\receipts", [int]$TimeoutSec = 900, [switch]$Dry)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_probe_common.ps1"
if (-not $Dry) { Assert-GpuFree }
$off = @(); $on = @()
foreach ($r in $Rows) { $off += @{ row = $r; tag = "r"; ref_cache = $false }; $on += @{ row = $r; tag = "r"; ref_cache = $true } }
$a = Invoke-Engine -Kit $Kit -Plan $off -Name "cache_off" -TimeoutSec $TimeoutSec -Dry:$Dry
$b = Invoke-Engine -Kit $Kit -Plan $on -Name "cache_on" -TimeoutSec $TimeoutSec -Dry:$Dry
if ($Dry) { Write-Host "Compare json would be $script:outDir\cache_multi_<stamp>.json"; exit 0 }
$cmp = "$script:outDir\cache_multi_" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + ".json"
& $script:py "$script:repo\bench\flux_multi.py" --compare $a $b $cmp --receipts-dir $Receipts
Write-Host "RESULT $cmp"
