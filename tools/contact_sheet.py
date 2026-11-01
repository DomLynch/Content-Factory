"""One contact sheet of all rendered cells of a kit: contact_sheet.py <kit.json> [out.jpg]
Cells come from outputs_dir/<kit>/<name>_<W>x<H>.png. Rows: bodies, heads, hands/feet, gear, swatches (like the reference
sheet); a missing cell is a labelled grey box. Total width 4096. An optional hero_*.png in the folder is added to the head row."""
import glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw

WIDTH, GAP, MAX_H = 4096, 8, 1100
LAYOUT = ["body", "head", "hands_feet", "gear", "swatch"]


def find(folder, name):
    hits = []
    for p in glob.glob(os.path.join(glob.escape(folder), glob.escape(name) + "_*x*.png")):
        size = os.path.basename(p)[len(name) + 1:-4]
        if size.replace("x", "", 1).isdigit():
            hits.append(p)
    return max(hits, key=os.path.getmtime) if hits else None


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    kit = json.load(open(sys.argv[1], encoding="utf-8-sig"))
    import cf_common
    outputs = os.environ.get("CF_OUTPUTS_DIR") or cf_common.load_config()["local"]["outputs_dir"]   # env override for tests
    folder = os.path.join(outputs, kit["kit"])
    cells = {g: [] for g in LAYOUT}
    for r in kit["rows"]:
        if r["group"] in cells:
            cells[r["group"]].append((r["name"], r.get("w", kit.get("w", 1024)), r.get("h", kit.get("h", 1536))))
    heroes = sorted(glob.glob(os.path.join(glob.escape(folder), "hero_*.png")))
    rows = []
    for g in LAYOUT:
        items = []
        for name, w, h in cells[g]:
            p = find(folder, name)
            im = Image.open(p).convert("RGB") if p else None
            items.append((name, im, im.width / im.height if im else w / h))
        if g == "head" and heroes:
            im = Image.open(heroes[-1]).convert("RGB")
            items.append(("hero", im, im.width / im.height))
        if items:
            rows.append(items)
    if not rows:
        raise SystemExit("kit has no cells")
    placed, total_h = [], GAP
    for items in rows:
        rh = min(MAX_H, int((WIDTH - GAP * (len(items) + 1)) / sum(a for _, _, a in items)))
        placed.append((items, rh))
        total_h += rh + GAP
    sheet = Image.new("RGB", (WIDTH, total_h), (24, 24, 26))
    d = ImageDraw.Draw(sheet)
    y = GAP
    for items, rh in placed:
        widths = [int(rh * a) for _, _, a in items]
        x = (WIDTH - sum(widths) - GAP * (len(items) - 1)) // 2
        for (name, im, _), cw in zip(items, widths):
            if im:
                sheet.paste(im.resize((cw, rh), Image.LANCZOS), (x, y))
            else:
                d.rectangle([x, y, x + cw - 1, y + rh - 1], fill=(96, 96, 96))
                d.text((x + 10, y + 10), f"MISSING: {name}", fill=(235, 235, 235))
            x += cw + GAP
        y += rh + GAP
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(folder, "contact_sheet.jpg")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sheet.save(out, quality=90)
    print(out)


if __name__ == "__main__":
    main()
