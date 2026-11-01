"""compare_kits.py <kit_a.json> <kit_b.json>: row-by-row diff (name, group, w, h, ref, use_ref, prompt, ref_prefix, seed, seed_note, mirror_of) plus kit-level fields.
Prints every difference; exit code 1 if any (kit name is reported but ignored)."""
import json, sys

ROW = ["group", "w", "h", "ref", "use_ref", "prompt", "ref_prefix", "seed", "seed_note", "mirror_of"]
KIT = ["seed", "w", "h", "template", "ref", "ref_prefix", "subject", "style", "description"]


def load(p):
    return json.load(open(p, encoding="utf-8-sig"))


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    diffs = 0
    print("kit names:", a["kit"], "|", b["kit"], "(ignored)")
    for k in KIT:
        if a.get(k) != b.get(k):
            diffs += 1; print(f"KIT {k}:\n  A={a.get(k)!r}\n  B={b.get(k)!r}")
    na, nb = [r["name"] for r in a["rows"]], [r["name"] for r in b["rows"]]
    if na != nb:
        diffs += 1; print("ROW NAMES/ORDER differ:", [n for n in na if n not in nb], [n for n in nb if n not in na])
    rb = {r["name"]: r for r in b["rows"]}
    for r in a["rows"]:
        o = rb.get(r["name"])
        if not o:
            continue
        for k in ROW:
            if r.get(k) != o.get(k):
                diffs += 1; print(f"ROW {r['name']} {k}:\n  A={r.get(k)!r}\n  B={o.get(k)!r}")
    print(f"{len(a['rows'])} vs {len(b['rows'])} rows, {diffs} differences")
    sys.exit(1 if diffs else 0)


main()
