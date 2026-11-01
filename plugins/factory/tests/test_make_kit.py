"""make_kit: every entry of the template's 'order' list must name a row that the Dracula and the knight specs really build."""
import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("make_kit", REPO / "tools" / "make_kit.py")
make_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_kit)
_cspec = importlib.util.spec_from_file_location("compare_kits", REPO / "tools" / "compare_kits.py")
compare_kits = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(compare_kits)

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


class HeadGateKeys(unittest.TestCase):
    """The Flux head gate (plugin 0.1.25) needs non-empty kit keys age and complexion on a humanoid kit."""

    def build(self, rel="characters/vampire-dracula-gpt-sheet.json", **over):
        spec = {**load(rel), **over}
        for k in [k for k, v in over.items() if v is None]:
            del spec[k]
        return make_kit.build(spec, load(spec["template"]))

    def test_v1_keys_come_from_kit_fields_and_prompts_stay_unchanged(self):
        spec = load("characters/vampire-dracula-gpt-sheet.json")
        kit = self.build()
        self.assertEqual(kit["age"], spec["kit_age"])
        self.assertEqual(kit["complexion"], spec["kit_complexion"])
        self.assertIs(kit["humanoid"], True)
        self.assertNotIn("head_age_desc", spec)
        self.assertFalse([r["name"] for r in kit["rows"] if "Age and face" in r["prompt"] or "Skin:" in r["prompt"]])

    def test_v2_keys_come_from_the_prompt_fields(self):
        spec = load("characters/vampire-dracula-gpt-sheet-v2.json")
        kit = self.build("characters/vampire-dracula-gpt-sheet-v2.json")
        self.assertEqual((kit["age"], kit["complexion"]), (spec["head_age_desc"], spec["head_skin_desc"]))
        self.assertIn(kit["age"], next(r["prompt"] for r in kit["rows"] if r["name"] == "head_front"))

    def test_kit_field_beats_prompt_field(self):
        kit = self.build("characters/vampire-dracula-gpt-sheet-v2.json", kit_age="kit wins", kit_complexion="also")
        self.assertEqual((kit["age"], kit["complexion"]), ("kit wins", "also"))

    def test_keys_are_also_on_every_head_row_and_only_there(self):
        kit = self.build()
        for r in kit["rows"]:
            if r["group"] == "head":
                self.assertEqual((r["age"], r["complexion"]), (kit["age"], kit["complexion"]), r["name"])
            else:
                self.assertNotIn("age", r, r["name"])

    def test_every_shipped_humanoid_spec_builds_with_both_keys(self):
        for rel in ("characters/vampire-dracula-gpt-sheet.json", "characters/vampire-dracula-gpt-sheet-v2.json",
                    "characters/knight-dryrun.json", "characters/_example_second_character.json"):
            kit = self.build(rel)
            self.assertTrue(kit["age"].strip() and kit["complexion"].strip(), rel)

    def test_missing_or_blank_field_is_an_error_naming_the_field(self):
        for over, name in (({"kit_age": None}, "kit_age or head_age_desc"), ({"kit_complexion": ""}, "kit_complexion or head_skin_desc"),
                           ({"kit_complexion": "   "}, "head_skin_desc")):
            with self.assertRaises(SystemExit) as cm:
                self.build(**over)
            msg = str(cm.exception)
            self.assertIn(name, msg)
            self.assertIn("humanoid", msg)

    def test_creature_spec_skips_the_requirement_and_copies_the_flag(self):
        kit = self.build(humanoid=False, kit_age=None, kit_complexion=None)
        self.assertIs(kit["humanoid"], False)
        self.assertNotIn("age", kit)
        self.assertNotIn("complexion", kit)


class CompareKitsIgnores(unittest.TestCase):
    def test_only_the_head_gate_keys_are_ignored(self):
        spec = load("characters/vampire-dracula-gpt-sheet.json")
        tpl = load(spec["template"])
        a = make_kit.build(spec, tpl)
        b = json.loads(json.dumps(a))
        b["age"], b["complexion"], b["humanoid"] = "x", "y", False
        for r in b["rows"]:
            if r["group"] == "head":
                r["age"] = "x"
        self.assertEqual(compare_kits.compare(a, b), [])
        b["rows"][0]["prompt"] += " changed"
        b["style"] = "other"
        self.assertEqual(len(compare_kits.compare(a, b)), 2)


if __name__ == "__main__":
    unittest.main()