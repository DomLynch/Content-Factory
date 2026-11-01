"""Keep Windows awake while jobs run (user level, no settings changed): holds SetThreadExecutionState until stopped.
Usage: keep_awake.py   (ends when the process is killed or the optional 'done file' argument appears)."""
import ctypes, os, sys, time
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
stop = sys.argv[1] if len(sys.argv) > 1 else None
while not (stop and os.path.exists(stop)):
    time.sleep(30)
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
