"""Measure how much GPU memory this machine really gives PyTorch (ROCm/Windows).

Reports: baseline readouts, largest single allocation, largest cumulative
footprint (capped), Windows dedicated/shared counters at the peak, and matmul
speed on dedicated vs spilled memory.   python tools/probe_gpu_mem.py [cap_gb]

On this stack a too-large fill raises hipErrorLaunchFailure and poisons the
context, so every trial runs in a fresh child process.
"""
import subprocess
import sys
import time

GB = 2**30
CAP_GB = 52
COUNTER_CMD = (
    "$c = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage','\\GPU Adapter Memory(*)\\Shared Usage' "
    "-ErrorAction SilentlyContinue; $c.CounterSamples | Where-Object { $_.CookedValue -gt 0 } | "
    "ForEach-Object { '{0} {1:N1}GB' -f (($_.Path -split 'gpu adapter memory\\(')[1] -replace 'luid_0x00000000_',''), ($_.CookedValue/1GB) }"
)


def counters():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", COUNTER_CMD],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return " | ".join(out.splitlines()) or "(no nonzero counters)"
    except Exception as e:  # counters are informational only
        return f"(counter read failed: {type(e).__name__})"


def child_single(gb):
    import torch
    try:
        t = torch.empty(int(gb * GB), dtype=torch.uint8, device="cuda")
        t.fill_(1)
        torch.cuda.synchronize()
    except (torch.cuda.OutOfMemoryError, RuntimeError):
        sys.exit(2)


def child_cumulative(n_gb):
    """Hold n_gb of 1 GB chunks, then time matmuls on early vs late tensors."""
    import torch
    dedicated_gb = torch.cuda.get_device_properties(0).total_memory / GB

    def timed(a, b, reps=5):
        torch.matmul(a, b)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(reps):
            torch.matmul(a, b)
        torch.cuda.synchronize()
        return (time.perf_counter() - t0) / reps * 1000

    try:
        a1 = torch.randn(8192, 8192, dtype=torch.bfloat16, device="cuda")
        b1 = torch.randn(8192, 8192, dtype=torch.bfloat16, device="cuda")
        held, a2, b2 = [], None, None
        for i in range(n_gb):
            c = torch.empty(GB, dtype=torch.uint8, device="cuda")
            c.fill_(1)
            held.append(c)
            if a2 is None and i + 1 > dedicated_gb + 1:
                a2 = torch.randn(8192, 8192, dtype=torch.bfloat16, device="cuda")
                b2 = torch.randn(8192, 8192, dtype=torch.bfloat16, device="cuda")
        torch.cuda.synchronize()
        peak = counters()
        ms_d = timed(a1, b1)
        ms_s = timed(a2, b2) if a2 is not None else float("nan")
        st = torch.cuda.memory_stats()
        print(f"PEAK {n_gb} | counters: {peak}")
        print(f"MATMUL dedicated {ms_d:.1f} ms | spilled {ms_s:.1f} ms")
        print(f"ALLOCATOR allocated {st['allocated_bytes.all.current'] / GB:.1f} GB | "
              f"reserved {st['reserved_bytes.all.current'] / GB:.1f} GB")
    except (torch.cuda.OutOfMemoryError, RuntimeError):
        sys.exit(2)


def trial(args, timeout=300):
    r = subprocess.run([sys.executable, "-I", __file__, *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def search(fn_args, lo, hi, step):
    """Largest value in [lo, hi] (multiple of step) whose trial exits 0; assumes monotone."""
    best = 0
    while hi - lo >= step:
        mid = (lo + hi) // 2 if step >= 1 else (lo + hi) / 2
        if trial(fn_args(mid))[0] == 0:
            best, lo = mid, mid + step
        else:
            hi = mid - step
    return best


def main():
    import torch
    props = torch.cuda.get_device_properties(0)
    free, tot = torch.cuda.mem_get_info()
    print(f"device: {props.name}")
    print(f"total_memory: {props.total_memory / GB:.1f} GB   mem_get_info free/total: {free / GB:.1f}/{tot / GB:.1f} GB")
    print(f"counters (idle): {counters()}", flush=True)
    single = search(lambda g: ["--single", str(g)], 1, 64, 1)
    print(f"largest single allocation (fill ok): {single} GB", flush=True)
    cum = search(lambda g: ["--cum", str(g)], 1, CAP_GB, 1)
    note = f"hit {CAP_GB} GB cap" if cum >= CAP_GB else "failed above this"
    print(f"cumulative 1 GB chunks (fill ok): {cum} GB ({note})", flush=True)
    code, out, err = trial(["--cum", str(cum)])
    print(out if code == 0 else f"re-run at {cum} GB failed: {err[-400:]}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--single":
        child_single(float(sys.argv[2]))
    elif len(sys.argv) > 2 and sys.argv[1] == "--cum":
        child_cumulative(int(sys.argv[2]))
    else:
        if len(sys.argv) > 1:
            CAP_GB = int(sys.argv[1])
        main()
