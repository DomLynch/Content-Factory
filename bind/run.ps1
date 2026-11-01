param($mesh,$out,$tag,$extra)
$a = @('--background','--python','C:\Users\dom12\Documents\agent_tests\bind2\bind_to_106.py','--',$mesh,$out) + $extra
$p = Start-Process -FilePath "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -ArgumentList $a -RedirectStandardOutput "$tag.log" -RedirectStandardError "$tag.err" -PassThru -NoNewWindow
if (-not $p.WaitForExit(290000)) { $p.Kill(); "KILLED at 290s" }
Select-String -Path "$tag.log" -Pattern '^R>|Error|Traceback|Warning' | % Line; Get-Content "$tag.err" | select -last 25
