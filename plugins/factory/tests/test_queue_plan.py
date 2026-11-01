"""tools/queue_plan.py: the pure decisions of bench/flux_queue.ps1 (order, hold, mirror, swatch crops, re-roll cap by asset_id, reference first)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("queue_plan", REPO / "tools" / "queue_plan.py")
qp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qp)


def row(name, group, **kw):
    return {"name": name, "row_id": name, "group": group, "prompt": f"prompt {name}", **kw}


KIT = {
    "kit": "k", "seed": 42, "w": 64, "h": 64, "ref": "", "ref_prefix": "ref:", "subject": "", "style": "style",
    "rows": [
        row("a_id", "alpha", identity=True),
        row("b_id", "beta", identity=True),
        row("a_two", "alpha"),
        row("b_two", "beta"),
        row("c_one", "gamma"),
        row("a_mirror", "alpha", mirror_of="a_two"),
        row("sw", "gamma", swatch_of={"source_row": "a_two", "box": [0, 0, 1, 1], "size": [8, 8]}),
    ],
}


class Env:
    """A throwaway repo: config, outputs dir, manifests and receipts."""

    def __init__(self, queue=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        (Path(self.repo) / "config").mkdir()
        self.out = Path(self.repo) / "out"
        (self.out / "k").mkdir(parents=True)
        cfg = {"defaults": {"seed": 42}}
        if queue is not None:
            cfg["queue"] = queue
        (Path(self.repo) / "config" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        (Path(self.repo) / "config" / "config.local.json").write_text(json.dumps({"outputs_dir": str(self.out)}), encoding="utf-8")

    def verdicts(self, **v):
        (Path(self.repo) / "manifests").mkdir(exist_ok=True)
        (Path(self.repo) / "manifests" / "k.json").write_text(json.dumps({"rows": {k: {"verdict": x} for k, x in v.items()}}), encoding="utf-8")

    def png(self, name):
        (self.out / "k" / f"{name}_64x64.png").write_bytes(b"x")

    def receipt(self, kit, r, n, status="ok", **over):
        prompt = qp.effective_prompt(kit, r)
        params = {"row_id": qp.row_id(r), "prompt": prompt, "seed": qp.effective_seed(kit, r), "ref_sha256": None, **over}
        d = Path(self.repo) / "receipts" / f"k-{qp.row_id(r)}-{n:06d}"
        d.mkdir(parents=True)
        (d / "flux-1.json").write_text(json.dumps({"status": status, "params": params}), encoding="utf-8")

    def decide(self, kit, rid):
        r = next(x for x in kit["rows"] if qp.row_id(x) == rid)
        return qp.decide(kit, r, self.repo)

    def close(self):
        self.tmp.cleanup()


class Plan(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)

    def test_order_is_the_kits_order(self):
        names = [r["row_id"] for r in qp.info(KIT, self.env.repo)["rows"]]
        self.assertEqual(names, [r["row_id"] for r in KIT["rows"]])

    def test_identity_cells_render_first_without_waiting(self):
        self.assertEqual(self.env.decide(KIT, "a_id")["action"], "render")
        self.assertEqual(self.env.decide(KIT, "b_id")["action"], "render")

    def test_sibling_waits_for_its_own_groups_identity(self):
        self.env.verdicts(a_id="PASS")
        self.assertEqual(self.env.decide(KIT, "a_two")["action"], "render")
        d = self.env.decide(KIT, "b_two")
        self.assertEqual((d["action"], d["wait_for"]), ("wait", ["b_id"]))

    def test_group_without_identity_waits_for_all(self):
        self.env.verdicts(a_id="PASS")
        d = self.env.decide(KIT, "c_one")
        self.assertEqual((d["action"], d["wait_for"]), ("wait", ["b_id"]))
        self.env.verdicts(a_id="PASS", b_id="PASS")
        self.assertEqual(self.env.decide(KIT, "c_one")["action"], "render")

    def test_only_pass_releases_a_row(self):
        for v in ("WEAK", "REJECT", ""):
            self.env.verdicts(a_id=v)
            self.assertEqual(self.env.decide(KIT, "a_two")["action"], "wait", v)
        self.env.verdicts(a_id="pass")   # case does not matter
        self.assertEqual(self.env.decide(KIT, "a_two")["action"], "render")

    def test_missing_manifest_waits(self):
        self.assertEqual(self.env.decide(KIT, "a_two")["action"], "wait")

    def test_mirror_rows_are_never_rendered(self):
        self.env.verdicts(a_id="PASS")
        d = self.env.decide(KIT, "a_mirror")
        self.assertEqual(d["action"], "skip")
        self.assertIn("mirror", d["reason"])

    def test_swatch_row_skipped_only_when_its_png_exists(self):
        self.env.verdicts(a_id="PASS", b_id="PASS")
        self.assertEqual(self.env.decide(KIT, "sw")["action"], "render")
        self.env.png("sw")
        d = self.env.decide(KIT, "sw")
        self.assertEqual((d["action"], d["reason"]), ("skip", "png exists"))

    def test_png_exists_skips(self):
        self.env.png("a_id")
        self.assertEqual(self.env.decide(KIT, "a_id")["reason"], "png exists")

    def test_hold_file_default_is_kit_keyed_under_outputs_dir(self):
        p = qp.info(KIT, self.env.repo)["hold_file"]
        self.assertEqual(Path(p), self.env.out / "_queue" / "k_hold.txt")
        (self.env.out / "_queue").mkdir()
        Path(p).write_text("# held\n\na_id\n", encoding="utf-8")
        self.assertEqual(self.env.decide(KIT, "a_id")["reason"], "hold file")
        self.assertEqual(self.env.decide(KIT, "b_id")["action"], "render")

    def test_hold_file_from_config(self):
        env = Env({"hold_file": ""})
        self.addCleanup(env.close)
        custom = Path(env.repo) / "my_hold.txt"
        (Path(env.repo) / "config" / "config.json").write_text(json.dumps({"queue": {"hold_file": str(custom)}}), encoding="utf-8")
        custom.write_text("b_id", encoding="utf-8")
        self.assertEqual(qp.info(KIT, env.repo)["hold_file"], str(custom))
        self.assertEqual(env.decide(KIT, "b_id")["action"], "skip")

    def test_queue_defaults(self):
        i = qp.info(KIT, self.env.repo)
        self.assertEqual((i["identity_wait_max_s"], i["identity_poll_s"], i["reroll_cap"]), (3600, 60, 2))

    def test_shipped_config_has_the_queue_block(self):
        cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(set(cfg["queue"]) - {"_comment"}, {"hold_file", "identity_wait_max_s", "identity_poll_s", "reroll_cap"})

    def test_shipped_kits_mark_their_identity_cells(self):
        for rel in ("kits/vampire_gpt_sheet_kit.json", "kits/vampire_gpt_sheet_v2_kit.json", "kits/knight_dryrun_kit.json"):
            kit = json.loads((REPO / rel).read_text(encoding="utf-8-sig"))
            self.assertEqual(qp.identity_ids(kit), ["head_front", "body_underlayer_front"], rel)
            self.assertEqual([r["row_id"] for r in kit["rows"][:2]], qp.identity_ids(kit), rel)


class RerollCap(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)
        self.env.verdicts(a_id="PASS", b_id="PASS")
        self.r = next(x for x in KIT["rows"] if x["row_id"] == "a_two")

    def test_one_ok_render_still_allowed(self):
        self.env.receipt(KIT, self.r, 1)
        self.assertEqual(self.env.decide(KIT, "a_two")["action"], "render")

    def test_two_ok_renders_of_the_same_asset_id_are_capped(self):
        self.env.receipt(KIT, self.r, 1)
        self.env.receipt(KIT, self.r, 2)
        d = self.env.decide(KIT, "a_two")
        self.assertEqual(d["action"], "skip")
        self.assertIn("REROLL_CAP", d["reason"])
        self.assertIn(d["asset_id"], d["reason"])

    def test_failed_receipts_do_not_count(self):
        self.env.receipt(KIT, self.r, 1)
        self.env.receipt(KIT, self.r, 2, status="fail")
        self.assertEqual(self.env.decide(KIT, "a_two")["action"], "render")

    def test_a_new_seed_prompt_or_reference_is_a_new_asset_id(self):
        self.env.receipt(KIT, self.r, 1)
        self.env.receipt(KIT, self.r, 2)
        reseeded = json.loads(json.dumps(KIT))
        next(x for x in reseeded["rows"] if x["row_id"] == "a_two")["seed"] = 43
        self.assertEqual(self.env.decide(reseeded, "a_two")["action"], "render")
        fixed = json.loads(json.dumps(KIT))
        next(x for x in fixed["rows"] if x["row_id"] == "a_two")["prompt"] += " with the braids stated"
        self.assertEqual(self.env.decide(fixed, "a_two")["action"], "render")
        ref = Path(self.env.repo) / "ref.png"
        ref.write_bytes(b"one")
        withref = json.loads(json.dumps(KIT))
        withref["ref"] = str(ref)
        self.assertEqual(self.env.decide(withref, "a_two")["action"], "render")   # receipts carry no ref sha: not the same asset_id

    def test_receipts_of_the_same_ref_bytes_cap_even_if_the_file_moved(self):
        ref = Path(self.env.repo) / "ref.png"
        ref.write_bytes(b"one")
        kit = json.loads(json.dumps(KIT))
        kit["ref"] = str(ref)
        r = next(x for x in kit["rows"] if x["row_id"] == "a_two")
        sha = qp.file_sha(str(ref))
        self.env.receipt(kit, r, 1, ref_sha256=sha)
        self.env.receipt(kit, r, 2, ref_sha256=sha)
        self.assertEqual(self.env.decide(kit, "a_two")["action"], "skip")
        ref.write_bytes(b"two")   # a changed reference is a new asset_id
        self.assertEqual(self.env.decide(kit, "a_two")["action"], "render")

    def test_cap_is_keyed_on_asset_id_not_on_the_row_name(self):
        # the old script capped by row name unless the row carried a seed: a seeded row is not exempt any more
        seeded = json.loads(json.dumps(KIT))
        s = next(x for x in seeded["rows"] if x["row_id"] == "a_two")
        s["seed"] = 44
        self.env.receipt(seeded, s, 1)
        self.env.receipt(seeded, s, 2)
        self.assertEqual(self.env.decide(seeded, "a_two")["action"], "skip")

    def test_configurable_cap(self):
        env = Env({"reroll_cap": 1})
        self.addCleanup(env.close)
        env.verdicts(a_id="PASS")
        env.receipt(KIT, self.r, 1)
        self.assertEqual(env.decide(KIT, "a_two")["action"], "skip")

    def test_effective_seed_prefers_the_row_seed(self):
        self.assertEqual(qp.effective_seed({"seed": 42}, {"seed": 44}), 44)
        self.assertEqual(qp.effective_seed({"seed": 42}, {}), 42)

    def test_prompt_matches_flux_kit_construction(self):
        kit = {**KIT, "ref": "x.png", "subject": "subj", "ref_prefix": "REF"}
        self.assertEqual(qp.effective_prompt(kit, {"prompt": "p"}), "REF p subj style")
        self.assertEqual(qp.effective_prompt(kit, {"prompt": "p", "use_ref": False, "use_subject": False}), "p  style")   # the empty subject leaves a double space, as in the receipts


class Cli(unittest.TestCase):
    def test_cli_decide_and_unknown_row(self):
        env = Env()
        self.addCleanup(env.close)
        kp = Path(env.repo) / "kit.json"
        kp.write_text(json.dumps(KIT), encoding="utf-8")
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = qp.main(["decide", str(kp), "a_id", "--repo", env.repo])
        self.assertEqual((rc, json.loads(buf.getvalue())["action"]), (0, "render"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = qp.main(["decide", str(kp), "nope", "--repo", env.repo])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
