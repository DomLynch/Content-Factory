$d="$env:USERPROFILE\Documents"
$vc = Get-ChildItem "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
# 1) dragon concept image, alone on the GPU
& "$d\Trellis-3-312\.venv\Scripts\python.exe" -u "$d\flux_env\flux_dragon.py" 2048 1536 *> "$d\flux_env\dragon_out.txt"
cmd /c "`"$vc`" >nul 2>&1 && set" | ForEach-Object { if($_ -match '^([^=]+)=(.*)$'){ [Environment]::SetEnvironmentVariable($Matches[1],$Matches[2],'Process') } }
$env:ATTN_BACKEND="sdpa"; $env:SPARSE_ATTN_BACKEND="sdpa"; $env:PYTHONUNBUFFERED="1"; $env:HF_HUB_OFFLINE="1"; $env:TRELLIS3_REMBG="none"
$img="$d\clean_sample\robot_1076.png"
$runs = @(
 @{tag='A_default'; dir='Trellis-3'; extra=''},
 @{tag='B_attn24'; dir='Trellis-3-312'; extra='--attn-auto-gb 24'},
 @{tag='B_attn16'; dir='Trellis-3-312'; extra='--attn-auto-gb 16'},
 @{tag='A_attn24'; dir='Trellis-3'; extra='--attn-auto-gb 24'},
 @{tag='B_tok98k'; dir='Trellis-3-312'; extra='--attn-auto-gb 24 --max-tokens 98304'}
)
foreach($r in $runs){
  Set-Location "$d\$($r.dir)"
  if($r.dir -eq 'Trellis-3-312'){ $env:SPARSE_CONV_BACKEND="flex_gemm"; $env:TRELLIS3_OVOXEL="native" } else { $env:SPARSE_CONV_BACKEND="none"; Remove-Item Env:TRELLIS3_OVOXEL -ErrorAction SilentlyContinue }
  $log="$d\attnbench_$($r.tag).txt"; $sw=[Diagnostics.Stopwatch]::StartNew()
  $a = @("generate.py",$img,"--preset","master","--texture-size","2048","--bake-faces","500000","--device","rocm","--single-process","--profile-memory","--output","bench_$($r.tag)")
  if($r.extra){ $a += ($r.extra -split ' ') }
  & ".venv\Scripts\python.exe" @a *>&1 | Out-File $log -Encoding utf8
  "WALL TIME: {0:N0} s  exit {1}" -f $sw.Elapsed.TotalSeconds,$LASTEXITCODE | Out-File $log -Append -Encoding utf8
}
'ALL_DONE' | Out-File "$d\queue2_done.txt"
