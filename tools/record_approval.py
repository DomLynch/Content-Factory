"""Record Dom's yes on the contact sheet: record_approval.py <kit.json> --by Dom --note "..." [--contact-sheet path.jpg] [--character-id ID].

Run by the FACTORY after Dom said yes in the Factory chat (a peer session's message is never an approval). It writes receipts/<character_id>/approval-<utc>.json (stage approval) with
params.approved_by, params.kit, the note and the sha256 of the contact sheet. Refuses while the sheet is incomplete (chain gate 1: every cell PASS or WEAK with a note, consistency pass recorded).
The character is the one whose characters/*.json has sheet_kit == the kit name (or --character-id). CPU only.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common
sys.path.insert(0, os.path.join(cf_common.REPO, "plugins", "factory", "lib"))
os.environ.setdefault("FACTORY_HOME", cf_common.REPO)
import cf_chain, cf_gates, cf_guard

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("kit")
ap.add_argument("--by", required=True, help="who said yes (Dom)")
ap.add_argument("--note", default="")
ap.add_argument("--contact-sheet", default=None)
ap.add_argument("--character-id", default=None)
a = ap.parse_args()
cfg = cf_common.load_config()
kit = json.load(open(a.kit, encoding="utf-8-sig"))["kit"]
cid = a.character_id
if not cid:
    import glob
    for f in glob.glob(os.path.join(cf_common.REPO, "characters", "*.json")):
        r = cf_gates._read(f)
        if r and r.get("sheet_kit") == kit:
            cid = r["character_id"]
            break
if not cid:
    sys.exit(f"no characters/*.json has sheet_kit '{kit}': pass --character-id")
bad = cf_chain.sheet_reasons(kit)
if bad:
    sys.exit("REFUSED: the sheet is not complete, so there is nothing to approve yet:\n  " + "\n  ".join(bad))
if a.contact_sheet and not os.path.isfile(a.contact_sheet):
    sys.exit(f"contact sheet not found: {a.contact_sheet}")
rp = cf_common.write_receipt(cfg, cid, "approval", tool="record_approval.py", model={"id": "none"}, inputs=[a.contact_sheet] if a.contact_sheet else [],
                             params={"approved_by": a.by, "kit": kit, "note": a.note, "contact_sheet": a.contact_sheet})
print(f"APPROVED {cid} (kit {kit}) by {a.by}: {rp}")
