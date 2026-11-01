"""make_kit: every entry of the template's 'order' list must name a row that the Dracula and the knight specs really build."""
import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("make_kit", REPO / "tools" / "make_kit.py")
make_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_kit)

SPECS = ["characters/vampire-dracula-gpt-sheet.json", "characters/_example_second_character.json"]


def load(rel):
    return json.loads((REPO / rel).read_text(encoding="utf-8-sig"))


class TemplateOrder(unittest.TestCase):
    def test_every_order_entry_names_a_row_or_group(self):
        for rel in SPECS:
            spec = load(rel)
            tpl = load(spec["template"])
            order = tpl.get("order")
            self.assertTrue(order, "template has no 'order' list")
            kit = make_kit.build(spec, tpl)
            ids = {r["row_id"] for r in kit["rows"]}
            groups = {r["group"] for r in kit["rows"]}
            for entry in order:
                if entry.startswith("group:"):
                    self.assertIn(entry[6:], groups, f"{rel}: order names unknown group {entry}")
                else:
                    self.assertIn(entry, ids, f"{rel}: order names a row the kit does not have: {entry}")
            self.assertEqual(len(order), len(set(order)), "duplicate entry in 'order'")

    def test_listed_rows_come_out_in_order(self):
        for rel in SPECS:
            spec = load(rel)
            tpl = load(spec["template"])
            names = [r["row_id"] for r in make_kit.build(spec, tpl)["rows"]]
            listed = [e for e in tpl["order"] if not e.startswith("group:")]
            self.assertEqual([n for n in names if n in listed], listed, rel)

    def test_unmatched_entry_prints_warning(self):
        import contextlib
        import io
        spec = load(SPECS[0])
        tpl = load(spec["template"])
        tpl["order"] = tpl["order"] + ["no_such_cell"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            make_kit.build(spec, tpl)
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("no_such_cell", buf.getvalue())


if __name__ == "__main__":
    unittest.main()