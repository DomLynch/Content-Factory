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

class OptionalAgeFields(unittest.TestCase):
    def test_v1_has_no_age_clauses_and_v2_has(self):
        v1 = make_kit.build(load("characters/vampire-dracula-gpt-sheet.json"), load("config/character_sheet_template_gpt.json"))
        v2 = make_kit.build(load("characters/vampire-dracula-gpt-sheet-v2.json"), load("config/character_sheet_template_gpt.json"))
        self.assertFalse([r["name"] for r in v1["rows"] if "Age and face" in r["prompt"] or "Skin:" in r["prompt"]])
        by = {r["name"]: r for r in v2["rows"]}
        for n in ("head_front", "head_three_quarter", "head_profile", "head_eyes_closeup"):
            self.assertIn("Age and face:", by[n]["prompt"], n)
        self.assertIn("Skin:", by["head_back"]["prompt"])
        self.assertIn("Build:", by["body_underlayer_front"]["prompt"])

    def test_v2_identity_lock_refs(self):
        spec = load("characters/vampire-dracula-gpt-sheet-v2.json")
        by = {r["name"]: r for r in make_kit.build(spec, load(spec["template"]))["rows"]}
        self.assertEqual(by["head_front"]["ref"], spec["head_front_ref"])
        for n in ("head_three_quarter", "head_profile", "head_back", "head_top"):
            self.assertEqual(by[n]["ref"], spec["head_lock_ref"], n)
        self.assertNotEqual(spec["head_lock_ref"], spec["head_front_ref"])
        self.assertTrue(all(by[n]["seed"] == 45 for n in ("head_front", "head_three_quarter", "head_profile", "head_back", "head_top")))


if __name__ == "__main__":
    unittest.main()