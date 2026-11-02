"""Fill config/character_sheet_template.json from a short character spec: make_kit.py <character_spec.json> [out_kit.json]
Writes kits/<id>_sheet_kit.json (or the given path). Fails if any {placeholder} is left unfilled."""
import json, os, string, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["id", "description", "seed", "ref", "base_layer_ref", "character_noun", "possessive", "facial_hair",
            "base_layer", "three_quarter_posture", "head_detail", "hair_desc", "gear_items", "swatches"]


YAW = {"head_front": 0, "head_three_quarter": 45, "head_profile": 90, "head_back": 180}   # magnitude in degrees (plugin 0.1.26: QC fails a head cell whose yaw does not match)
ANGLE = ("body_back", "body_left", "body_right", "body_three_quarter", "head_three_quarter", "head_profile", "head_back")   # angle cells use the whole two-view sheet image as reference
PASS_KEYS = ("needs", "supplied_by", "file", "yaw_deg", "ref_mode", "ref_max_px", "mirror_of")


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
    kit = {"kit": spec["id"] + "_sheet", "seed": spec["seed"], "w": kd["w"], "h": kd["h"],
           "template": "config/character_sheet_template.json", "ref": spec["ref"],
           "ref_prefix": fill(kd["ref_prefix"], base, "kit ref_prefix"),
           "subject": spec.get("subject", ""), "style": spec.get("style", kd["style"]),
           "description": spec["description"], "rows": []}
    for k in ("age", "complexion", "humanoid"):   # the head rows need them (0.1.25 gate); a creature spec says humanoid: false
        if k in spec:
            kit[k] = spec[k]
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
            row["needs"] = []   # rows that read another row's output list it here; the queue holds until it has a verdict
            row["ref_mode"] = "sheet" if rid in ANGLE else "identity"
            if rid in YAW:
                row["yaw_deg"] = YAW[rid]
            row.update({k: cell[k] for k in PASS_KEYS if k in cell})
            row.update((spec.get("row_overrides") or {}).get(rid, {}))   # e.g. {"hands": {"supplied_by": "Dom", "file": "C:\\...\\hands.png"}}
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
    tpl = json.load(open(os.path.join(REPO, "config", "character_sheet_template.json"), encoding="utf-8-sig"))
    kit = build(spec, tpl)
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "kits", spec["id"] + "_sheet_kit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(kit, f, indent=1, ensure_ascii=False)
    print(out, len(kit["rows"]), "rows")


if __name__ == "__main__":
    main()
