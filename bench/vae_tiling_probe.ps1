# VAE tiling probe (plugin 0.1.23): one process, one NO-reference cell (default gear_cuffs, same seed), decoded tiled, then untiled (slicing as configured),
# then tiled again to show drift. Reports decode seconds, gen_s and the pixel diff of untiled vs the first tiled job (untiled is the more exact decode: tiling
# blends seams). Output only to <outputs_dir>\_speed_tests\.
# Usage: powershell -NoProfile -File bench\vae_tiling_probe.ps1 -Kit <kit.json> [-Row gear_cuffs] [-TimeoutSec 600] [-Dry]
param([Parameter(Mandatory=$true)][string]$Kit, [string]$Row = "gear_cuffs", [int]$TimeoutSec = 600, [switch]$Dry)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_probe_common.ps1"
if (-not $Dry) { Assert-GpuFree }
$plan = @(
  @{ row = $Row; tag = "tiled";   use_ref = $false; vae_tiling = $true },
  @{ row = $Row; tag = "untiled"; use_ref = $false; vae_tiling = $false },
  @{ row = $Row; tag = "tiled2";  use_ref = $false; vae_tiling = $true })
$r = Invoke-Engine -Kit $Kit -Plan $plan -Name "vae_tiling" -TimeoutSec $TimeoutSec -Dry:$Dry
if ($r) { Write-Host "RESULT $r" }
