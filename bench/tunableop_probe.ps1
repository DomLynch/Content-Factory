# PyTorch TunableOp probe on this ROCm wheel (plugin 0.1.23). One process, one NO-reference cell (default gear_cuffs) rendered three times with the same seed:
#   base (TunableOp off)  ->  tune (tuning on, writes the csv)  ->  reuse (tuning off, the csv reused).
# Reports: whether the wheel supports it (csv written and non-empty, or the exact error), s/step for each job, and the pixel diff of tune/reuse vs base
# (GEMM kernels swap, so judge QC-equal on the mean abs pixel diff, not byte-identical). Output only to <outputs_dir>\_speed_tests\.
# Usage: powershell -NoProfile -File bench\tunableop_probe.ps1 -Kit <kit.json> [-Row gear_cuffs] [-TimeoutSec 1500] [-Dry]
# Tuning is slow (it benchmarks many GEMM shapes): the default hard timeout is 25 minutes; a timeout keeps the jobs that finished (the json is rewritten after every job).
param([Parameter(Mandatory=$true)][string]$Kit, [string]$Row = "gear_cuffs", [int]$TimeoutSec = 1500, [switch]$Dry)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_probe_common.ps1"
if (-not $Dry) { Assert-GpuFree }
$plan = @(
  @{ row = $Row; tag = "base";  use_ref = $false; tunable = "off" },
  @{ row = $Row; tag = "tune";  use_ref = $false; tunable = "tune" },
  @{ row = $Row; tag = "reuse"; use_ref = $false; tunable = "reuse" })
$r = Invoke-Engine -Kit $Kit -Plan $plan -Name "tunableop" -TimeoutSec $TimeoutSec -Dry:$Dry
if ($r) { Write-Host "RESULT $r  (csv next to it: *_tunableop.csv)" }
