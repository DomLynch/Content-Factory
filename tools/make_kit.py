"""Fill a character sheet template from a short character spec: make_kit.py <character_spec.json> [out_kit.json] [--kit-name NAME]
Writes kits/<kit_name>_kit.json (or the given path). Fails if any {placeholder} is left unfilled.
Character words live only in the spec; a template may list extra "required_fields" and "group_defaults"
(row keys such as use_ref applied to every cell of a group unless the cell sets them itself).
"derived" defines optional clauses ({"name": {"from": field, "prefix": text}}); a cell may have "forms" (prompt variants chosen by the item's "form").
For every string spec field X, {X} and a capitalised {X} (first letter upper, e.g. {Possessive}) are available."""
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
    miss = [k for k in REQUIRED + tpl.get("required_fields", []) if k not in spec]
    if miss:
        raise SystemExit(f"spec is missing fields: {miss}")
    kd = tpl["kit_defaults"]
    base = {**tpl.get("item_defaults", {}), **{k: v for k, v in spec.items() if isinstance(v, (str, int))}}
    base.update({k[:1].upper() + k[1:]: v[:1].upper() + v[1:] for k, v in list(base.items()) if isinstance(v, str) and k[:1].upper() + k[1:] not in base})
    gdef = tpl.get("group_defaults", {})
    derived = tpl.get("derived", {})
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
            for dn, dd in derived.items():   # optional clauses: prefix + value, or "" when the value is empty/missing
                ctx[dn] = dd.get("prefix", "") + str(ctx[dd["from"]]) if ctx.get(dd["from"]) else ""
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
            use_ref = cell.get("use_ref", gdef.get(cell["group"], {}).get("use_ref"))
            if use_ref is not None:
                row["use_ref"] = use_ref
            ptext = cell["forms"][ctx.get("form", cell.get("default_form"))] if "forms" in cell else cell["prompt"]
            row["prompt"] = fill(ptext, ctx, where)
            rs = spec.get("row_seeds", {}).get(rid)   # per-row re-render seed with the reason it was changed
            if rs:
                row["seed"], row["seed_note"] = rs["seed"], rs["note"]
            if cell.get("mirror_of"):   # made by tools/mirror_cell.py from another row, not rendered
                row["mirror_of"] = cell["mirror_of"]
            rows.append(row)
    if tpl.get("order"):   # explicit emit order: row_id or "group:<g>" entries, a row_id beats its group, unlisted rows last
        pos = {e: i for i, e in enumerate(tpl["order"])}
        rank = lambda r: pos.get(r["row_id"], pos.get("group:" + r["group"], len(pos)))
    else:
        order = {g: i for i, g in enumerate(tpl["group_priority"])}
        rank = lambda r: order[r["group"]]
    kit["rows"] = sorted(rows, key=rank)   # stable: GPU priority order
    ids = [r["row_id"] for r in kit["rows"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate row_id in generated kit")
    return kit


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    args = sys.argv[1:]
    if "--kit-name" in args:
        i = args.index("--kit-name")
        kit_name = args[i + 1]
        del args[i:i + 2]
    else:
        kit_name = None
    spec = json.load(open(args[0], encoding="utf-8-sig"))
    if kit_name:
        spec["kit_name"] = kit_name
    tpl = json.load(open(os.path.join(REPO, spec.get("template", "config/character_sheet_template.json")), encoding="utf-8-sig"))
    kit = build(spec, tpl)
    out = args[1] if len(args) > 1 else os.path.join(REPO, "kits", spec.get("kit_name", spec["id"] + "_sheet") + "_kit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(kit, f, indent=1, ensure_ascii=False)
    print(out, len(kit["rows"]), "rows")


if __name__ == "__main__":
    main()
