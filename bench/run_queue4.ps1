$d="$env:USERPROFILE\Documents"; $py="$d\Trellis-3-312\.venv\Scripts\python.exe"
function RunGuarded($tag, $script, $args2, $out) {
  $g = Start-Process powershell -ArgumentList "-NoProfile","-File","$d\gpu_guard.ps1","-Match",$tag,"-MinFreeRamGB","2","-MaxGpuGB","0" -PassThru -WindowStyle Hidden
  $sw=[Diagnostics.Stopwatch]::StartNew()
  & $py -u $script @args2 *>&1 | Out-File $out -Encoding utf8
  "WALL {0:N0} s" -f $sw.Elapsed.TotalSeconds | Out-File $out -Append -Encoding utf8
  if(-not $g.HasExited){ Stop-Process $g.Id -Force -ErrorAction SilentlyContinue }
}
# 1) tiled 2048x1536 rerun, text encoder offloaded, lid open
RunGuarded "flux_tile_test" "$d\flux_env\flux_tile_test.py" @("tiled") "$d\flux_env\tile_out2.txt"
# 2) environment shots at 1.5 MP
RunGuarded "flux_env_shots" "$d\flux_env\flux_env_shots.py" @("1664","928") "$d\flux_env\env_out.txt"
'STAGE1_DONE' | Out-File "$d\queue4_stage1_done.txt"
