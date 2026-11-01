"""Side-by-side contact sheet of the two matched turntables (plugin 0.1.14, rough). Columns are the blind labels A and B (engines never named); rows are the turntable angles, then the
face and hair crops. PIL if importable in the factory python, else a documented fallback: contact_sheet.html (the same grid as an HTML page that references the png files).
make(engines_out, label_map, out_dir) -> path of the sheet, or None when no images were rendered."""
import html
import os


def _rows(engines_out, lm):
    by_label = {lm[e]: v for e, v in engines_out.items() if e in lm}
    cols = {l: list((by_label.get(l, {}).get("images") or {}).get("frames") or []) for l in ("A", "B")}
    rows = [(cols["A"][i] if i < len(cols["A"]) else None, cols["B"][i] if i < len(cols["B"]) else None) for i in range(max(len(cols["A"]), len(cols["B"]), 0))]
    for k in ("face_crop", "hair_crop"):
        rows.append(tuple((by_label.get(l, {}).get("images") or {}).get(k) for l in ("A", "B")))
    return [r for r in rows if any(r)]


def make(engines_out, lm, out_dir):
    rows = _rows(engines_out, lm)
    if not rows:
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        path = os.path.join(out_dir, "contact_sheet.html")
        def cell(p):
            return "<td>" + ('<img src="%s" width=384>' % html.escape(os.path.abspath(p)) if p else "") + "</td>"
        cells = "".join("<tr>" + "".join(cell(p) for p in r) + "</tr>" for r in rows)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"<html><body><table><tr><th>A</th><th>B</th></tr>{cells}</table></body></html>")
        return path
    thumb_w = 384
    tiles = []
    for r in rows:
        row = []
        for p in r:
            if p and os.path.exists(p):
                im = Image.open(p).convert("RGB")
                row.append(im.resize((thumb_w, round(im.height * thumb_w / im.width))))
            else:
                row.append(None)
        tiles.append(row)
    heights = [max((t.height for t in row if t), default=0) for row in tiles]
    sheet = Image.new("RGB", (2 * thumb_w + 30, sum(heights) + 30 + 24 * len(rows)), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    y = 24
    d.text((10, 6), "A", fill=(255, 255, 255))
    d.text((thumb_w + 20, 6), "B", fill=(255, 255, 255))
    for row, h in zip(tiles, heights):
        for i, t in enumerate(row):
            if t:
                sheet.paste(t, (10 + i * (thumb_w + 10), y))
        y += h + 24
    path = os.path.join(out_dir, "contact_sheet.png")
    sheet.save(path)
    return path
