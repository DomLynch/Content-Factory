"""Replace duplicate files in a Hugging Face hub cache snapshot with symlinks into blobs/ (needs Windows Developer Mode).
No download: a snapshot file is replaced only when an identical blob (same hash name, same size) already exists.
Usage: hf_relink.py <repo cache folder, e.g. ...\\hub\\models--microsoft--TRELLIS.2-4B> [--apply]
Without --apply it only reports what it would do. Each replacement is atomic (symlink made next to the file, then os.replace); files that are
open (a job reading them) are skipped and reported, run again later."""
import hashlib, os, sys


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def git_sha1(p):
    size = os.path.getsize(p)
    h = hashlib.sha1(b"blob %d\0" % size)
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


root = sys.argv[1]
apply = "--apply" in sys.argv
blobs = os.path.join(root, "blobs")
have = {n: os.path.getsize(os.path.join(blobs, n)) for n in os.listdir(blobs) if not n.endswith(".incomplete")}
freed = would = skipped = 0
for dp, _, fns in os.walk(os.path.join(root, "snapshots")):
    for fn in fns:
        p = os.path.join(dp, fn)
        if os.path.islink(p):
            continue
        size = os.path.getsize(p)
        name = None
        for fn_hash in (sha256, git_sha1):
            hx = fn_hash(p)
            if hx in have and have[hx] == size:
                name = hx
                break
        if not name:
            print(f"NO BLOB (kept as file): {os.path.relpath(p, root)} {size/2**20:.1f} MB")
            skipped += 1
            continue
        would += size
        if not apply:
            print(f"would link {os.path.relpath(p, root)} -> blobs/{name[:12]}... ({size/2**30:.2f} GB)")
            continue
        tmp = p + ".lnk_tmp"
        try:
            if os.path.lexists(tmp):
                os.remove(tmp)
            os.symlink(os.path.relpath(os.path.join(blobs, name), dp), tmp)
            if os.path.getsize(tmp) != size:
                raise OSError("symlink does not resolve to a blob of the same size")
            os.replace(tmp, p)
            freed += size
            print(f"linked {os.path.relpath(p, root)} ({size/2**30:.2f} GB freed)")
        except OSError as e:
            if os.path.lexists(tmp):
                os.remove(tmp)
            print(f"SKIPPED (kept as file) {os.path.relpath(p, root)}: {e}")
            skipped += 1
print(f"{'freed' if apply else 'would free'} {(freed if apply else would)/2**30:.2f} GB, {skipped} file(s) kept as files")
