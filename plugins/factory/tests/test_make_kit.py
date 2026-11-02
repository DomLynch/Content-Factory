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
            inactive = {c["row_id"] for c in tpl["cells"] if c.get("requires_field") and not spec.get(c["requires_field"])}
            groups = {r["group"] for r in kit["rows"]}
            for entry in order:
                if entry in inactive:
                    continue
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
            listed = [e for e in tpl["order"] if not e.startswith("group:") and e in names]
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
        self.assertEqual(by["head_front"]["ref"], spec["identity_ref"])
        for n in ("head_three_quarter", "head_profile", "head_back", "head_top"):
            self.assertEqual(by[n]["ref"], spec["head_lock_ref"], n)
        self.assertNotEqual(spec["head_lock_ref"], spec["identity_ref"])
        self.assertTrue(all(by[n]["seed"] == 45 for n in ("head_front", "head_three_quarter", "head_profile", "head_back", "head_top")))


    def test_v2_gear_flats_use_parts_ref_for_shape_only(self):
        spec = load("characters/vampire-dracula-gpt-sheet-v2.json")
        by = {r["name"]: r for r in make_kit.build(spec, load(spec["template"]))["rows"]}
        self.assertEqual(by["gear_coat"]["ref"], spec["parts_ref"])
        self.assertIn("only for the cut and shape", by["gear_coat"]["ref_prefix"])
        self.assertNotIn("swatch_of", by["swatch_black_fur"])
        self.assertIs(by["swatch_black_fur"]["use_ref"], False)

    def test_optional_shirt_cells_only_for_characters_that_fill_them(self):
        tpl = load("config/character_sheet_template_gpt.json")
        counts = {}
        for rel in ("characters/vampire-dracula-gpt-sheet.json", "characters/vampire-dracula-gpt-sheet-v2.json", "characters/_example_second_character.json"):
            names = {r["name"] for r in make_kit.build(load(rel), tpl)["rows"]}
            counts[rel] = (len(names), {"gear_shirt", "gear_shirt_collar", "gear_shirt_cuffs"} <= names)
        self.assertEqual(counts["characters/vampire-dracula-gpt-sheet.json"], (43, False))
        self.assertEqual(counts["characters/vampire-dracula-gpt-sheet-v2.json"], (46, True))
        self.assertTrue(counts["characters/_example_second_character.json"][1])

    def test_v2_kit_records_reference_hashes_and_default_underlayer(self):
        spec = load("characters/vampire-dracula-gpt-sheet-v2.json")
        kit = make_kit.build(spec, load(spec["template"]))
        self.assertEqual(kit["refs"]["identity_ref"]["sha256"], spec["identity_ref_sha256"])
        self.assertNotIn("back_ref", kit["refs"])
        v1 = load("characters/vampire-dracula-gpt-sheet.json")
        self.assertNotIn("refs", make_kit.build(v1, load(v1["template"])))
        tpl = load("config/character_sheet_template_gpt.json")
        s2 = dict(spec); s2.pop("body_underlayer_desc")
        row = next(r for r in make_kit.build(s2, tpl)["rows"] if r["name"] == "body_underlayer_left")
        self.assertIn("fitted plain dark briefs, bare torso, arms and legs, " + spec["body_skin_desc"], row["prompt"])


if __name__ == "__main__":
    unittest.main()