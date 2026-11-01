---
description: Show Content-Factory status: GPU lock, running factory jobs, queue scripts, latest receipts, free RAM. Read-only.
allowed-tools: Bash(powershell:*), Read, Glob
---

Report the state of the Content-Factory (repo `C:\Users\dom12\Documents\Content-Factory`). This command only reads; it never starts a stage, a GPU job, Blender, Flux or TRELLIS.

1. **GPU lock.** Read `C:\Users\dom12\Documents\.gpu_lock` if it exists (JSON: `pid`, `job`, `started_utc`). Check whether that pid is alive with `Get-Process -Id <pid>`. Report one of: no lock (GPU free), lock with live pid (name the job and how long it has run; a new GPU job must wait), stale lock (dead pid; `tools/gpu_guard.ps1` removes it at next start, do not delete it by hand).
2. **Running factory processes.** `Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='blender.exe'" | Where-Object { $_.CommandLine -match 'flux_kit|generate\.py|run_3d|bind_best|blender' } | Select-Object ProcessId,CommandLine`. Report any that are not covered by the lock.
3. **Free RAM.** `(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB` in GB. The guard kills a job under 2 GB; warn if under 3 GB with a job running, and say a new heavy job should not start.
4. **Queue scripts.** List `bench\run_kit.ps1` and `bench\run_queue*.ps1` with their last-write time, and read `Documents\kit_*_done.txt` markers if present. Say which queue, if any, looks active (a matching process in step 2).
5. **Latest receipts.** In `receipts\`, find the 10 newest `*.json` by last-write time (recurse). For each show asset folder, stage, `status`, `timing.wall_s`, `memory.lowest_free_ram_gb` and `error` if status is not `ok`. Flag any `fail`.
6. **Last guard events.** Last 10 lines of `C:\Users\dom12\Documents\crash_monitor.csv`; flag `GUARD_ABORT`.
7. **Summary.** End with one line: GPU free or busy, RAM free, newest receipt, and whether anything needs attention.

Paths here are the defaults from `config/config.local.json`; if they differ, read `config/config.local.json` and use its `work_dir`, `outputs_dir`, `logs_dir` and `monitor_csv`.

(Draft: written from the repo's scripts; this command has not yet been run inside Claude Code.)
