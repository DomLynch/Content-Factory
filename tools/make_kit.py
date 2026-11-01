"""Fill config/character_sheet_template.json from a short character spec: make_kit.py <character_spec.json> [out_kit.json]
Writes kits/<id>_sheet_kit.json (or the given path). Fails if any {placeholder} is left unfilled."""
import json, os, string, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["id", "description", "seed", "ref", "base_layer_ref", "character_noun", "possessive", "facial_hair",
            "base_layer", "three_quarter_posture", "head_detail", "hair_desc", "gear_items", "swatches"]


def fill(text, ctx, where):
    names = [f for _, f, _, _ in string.Formatter().parse(text) if f]
    missing = sorted({n for n in names if n not in ctx})
    if missing:
        raise SystemExit(f"{where}: unfilled placeholders {missing}")
    return text.format_map(ctx)


def build(spec, tpl):
    miss = [k for k in REQUIRED if k not in spec]
    if miss:
        raise SystemExit(f"spec is missing fields: {miss}")
    kd = tpl["kit_defaults"]
    base = {**tpl.get("item_defaults", {}), **{k: v for k, v in spec.items() if isinstance(v, (str, int))}}
    kit = {"kit": spec.get("kit_name", spec["id"] + "_sheet"), "seed": spec["seed"], "w": kd["w"], "h": kd["h"],
           "template": spec.get("template", "config/character_sheet_template.json"), "ref": spec["ref"],
           "ref_prefix": fill(kd["ref_prefix"], base, "kit ref_prefix"),
           "subject": spec.get("subject", ""), "style": spec.get("style", kd["style"]),
           "description": spec["description"], "rows": []}
    rows = []
    for cell in tpl["cells"]:
        items = [None] if "repeat" not in cell else spec[cell["repeat"]]
        for item in items:
            ctx = {**base, **(item or {})}
            rid = fill(cell["row_id"], ctx, "row_id")
            where = "row " + rid
            row = {"name": rid, "row_id": rid, "group": cell["group"]}
            size = (item or {}).get("size", cell.get("size"))
            if size:
                row["w"], row["h"] = size
            if cell.get("ref"):
                row["ref"] = fill(cell["ref"], ctx, where)
            if cell.get("ref_prefix"):
                row["ref_prefix"] = fill(cell["ref_prefix"], ctx, where)
            if "use_ref" in cell:
                row["use_ref"] = cell["use_ref"]
            row["prompt"] = fill(cell["prompt"], ctx, where)
            rows.append(row)
    order = {g: i for i, g in enumerate(tpl["group_priority"])}
    kit["rows"] = sorted(rows, key=lambda r: order[r["group"]])   # stable: GPU priority order
    ids = [r["row_id"] for r in kit["rows"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate row_id in generated kit")
    return kit


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    spec = json.load(open(sys.argv[1], encoding="utf-8-sig"))
    tpl = json.load(open(os.path.join(REPO, spec.get("template", "config/character_sheet_template.json")), encoding="utf-8-sig"))
    kit = build(spec, tpl)
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "kits", spec.get("kit_name", spec["id"] + "_sheet") + "_kit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(kit, f, indent=1, ensure_ascii=False)
    print(out, len(kit["rows"]), "rows")


if __name__ == "__main__":
    main()
