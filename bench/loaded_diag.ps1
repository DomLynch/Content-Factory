# Loaded-path diagnosis switchboard (plugin 0.1.23): find why the keep-model-loaded path doubled gen_s (168 s vs 67-76 s) in ONE window.
# One process (the model loaded once, like flux_kit_fast.py), one REFERENCED cell (default head_front) rendered 6 times with the same seed, one switch changed per job:
#   base  ->  nogc (skip the per-row gc + empty_cache)  ->  notile (VAE tiling off)  ->  outlist (output=['images'] only)  ->  nan (isnan on latents and decoded)  ->  base
# gen_s, s/step, VAE decode seconds and the pixel diff vs the first base are printed per job; the last base against the first shows plain drift of a loaded process.
# Each switch is independent (never combined). Output only to <outputs_dir>\_speed_tests\.
# Usage: powershell -NoProfile -File bench\loaded_diag.ps1 -Kit <kit.json> [-Row head_front] [-Variants base,nogc,notile,outlist,nan,base] [-TimeoutSec 1200] [-Dry]
param([Parameter(Mandatory=$true)][string]$Kit, [string]$Row = "head_front", [string[]]$Variants = @("base","nogc","notile","outlist","nan","base"), [int]$TimeoutSec = 1200, [switch]$Dry)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_probe_common.ps1"
if (-not $Dry) { Assert-GpuFree }
$plan = @()
foreach ($v in $Variants) {
  $j = @{ row = $Row; tag = $v }
  switch ($v) {
    "nogc"    { $j.skip_gc = $true }
    "notile"  { $j.vae_tiling = $false }
    "outlist" { $j.out_list = $true }
    "nan"     { $j.nan_diag = $true }
    "base"    { }
    default   { Write-Error "unknown variant $v (base, nogc, notile, outlist, nan)"; exit 2 }
  }
  $plan += $j
}
$r = Invoke-Engine -Kit $Kit -Plan $plan -Name "loaded_diag" -TimeoutSec $TimeoutSec -Dry:$Dry
if ($r) { Write-Host "RESULT $r" }
