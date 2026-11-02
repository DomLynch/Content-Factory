# Self-test for gpu_guard.ps1: starts a RAM hog and checks the guard kills it and logs GUARD_ABORT. Run only with no GPU job active.
$d = "$env:USERPROFILE\Documents"; $repo = Split-Path -Parent $PSScriptRoot
$loc = Get-Content "$repo\config\config.local.json" -Raw | ConvertFrom-Json
$t = Join-Path $env:TEMP "guard_selftest"; New-Item -ItemType Directory -Force $t | Out-Null
"import time`nb=[]`nfor i in range(40):`n    b.append(b'x'*(1<<30)); print('held',i+1,flush=True); time.sleep(1.5)`nprint('SURVIVED')" | Set-Content "$t\ramhog_selftest.py" -Encoding ascii
$free = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1); $min = [math]::Round($free-3.5,1)
$hog = Start-Process $loc.python -ArgumentList "-I","-u","$t\ramhog_selftest.py" -PassThru -RedirectStandardOutput "$t\out.txt" -WindowStyle Hidden
$g = Start-Process powershell -ArgumentList "-NoProfile","-File",$loc.guard_script,"-Match","ramhog_selftest","-MinFreeRamGB",$min,"-MaxGpuGB",0 -PassThru -WindowStyle Hidden
$hog.WaitForExit(90000) | Out-Null
Start-Sleep 3
$ok = (Select-String $loc.monitor_csv -Pattern "GUARD_ABORT,job 'ramhog_selftest'" | Select-Object -Last 1) -and $hog.HasExited
if(-not $hog.HasExited){ Stop-Process $hog.Id -Force }
if(-not $g.HasExited){ Stop-Process $g.Id -Force }
$last = (Get-Content "$t\out.txt" -Tail 1)
"GUARD SELFTEST: " + $(if($ok){"PASS"}else{"FAIL"}) + " (free was $free GB, limit $min GB, hog reached: $last)"
