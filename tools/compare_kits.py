"""compare_kits.py <kit_a.json> <kit_b.json> [--ignore-order]: row-by-row diff of every row key and every kit-level key.
Prints every difference; exit code 1 if any. Ignored: the kit name, and exactly the head-gate keys age, complexion and humanoid
(kit level, and age/complexion on head rows), which a regenerated kit gains without any prompt text changing."""
import json, sys

IGNORED = {"age", "complexion", "humanoid"}
ROW_SKIP = {"name", "row_id"} | IGNORED
KIT_SKIP = {"kit", "rows"} | IGNORED


def load(p):
    return json.load(open(p, encoding="utf-8-sig"))


def compare(a, b, ignore_order=False):
    """Differences between two kits as printable blocks."""
    out = []
    for k in sorted((set(a) | set(b)) - KIT_SKIP):
        if a.get(k) != b.get(k):
            out.append(f"KIT {k}:\n  A={a.get(k)!r}\n  B={b.get(k)!r}")
    na, nb = [r["name"] for r in a["rows"]], [r["name"] for r in b["rows"]]
    if (sorted(na) != sorted(nb)) if ignore_order else (na != nb):
        out.append(f"ROW NAMES/ORDER differ: {[n for n in na if n not in nb]} {[n for n in nb if n not in na]}")
    rb = {r["name"]: r for r in b["rows"]}
    for r in a["rows"]:
        o = rb.get(r["name"])
        if not o:
            continue
        for k in sorted((set(r) | set(o)) - ROW_SKIP):
            if r.get(k) != o.get(k):
                out.append(f"ROW {r['name']} {k}:\n  A={r.get(k)!r}\n  B={o.get(k)!r}")
    return out


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    print("kit names:", a["kit"], "|", b["kit"], "(ignored)")
    diffs = compare(a, b, "--ignore-order" in sys.argv)
    for d in diffs:
        print(d)
    print(f"{len(a['rows'])} vs {len(b['rows'])} rows, {len(diffs)} differences")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
