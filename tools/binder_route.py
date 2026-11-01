"""Which binder runs: SkinTokens (target default) or the donor transfer (labelled fallback). Pure functions, standard library only, no job is started here.

config/config.json -> bind.binder is 'skin_tokens' or 'donor'; bind.fallback_to_donor (default true) lets a SkinTokens bind that failed, or whose prerequisites are
missing, fall back to the donor bind. A fallback is never silent: the donor receipt carries params.binder_used = 'donor' and params.label = FALLBACK_LABEL.
Used by tools/run_character.py (bind unit), tools/run_bind.py (labels), tools/assemble_character.py (which GLB is the bound body) and the MCP tool `bind`.
Flip the default with tools/set_default_binder.py."""
import json
import os

BINDERS = ("skin_tokens", "donor")
FALLBACK_LABEL = "FALLBACK donor (MakeHuman weights)"
DONOR_LABEL = "donor (MakeHuman weights)"
POINTER = "bound_source.json"
REASON_ENV = "BIND_FALLBACK_REASON"


def binder(cfg):
    """The configured binder; anything but the two known values is an error (a typo must not silently pick one)."""
    value = cfg["bind"].get("binder", "donor")
    if value not in BINDERS:
        raise ValueError(f"config bind.binder is {value!r}; use one of {list(BINDERS)}")
    return value


def fallback_allowed(cfg):
    return bool(cfg["bind"].get("fallback_to_donor", True))


def skin_tokens_missing(cfg, repo, skeleton=None):
    """Prerequisites of tools/run_skin_tokens.py that do not exist, as 'name: path' strings ([] = ready). Mirrors the checks that script makes before it starts."""
    loc, st = cfg["local"], cfg["skin_tokens"]
    repo_dir = loc.get("skin_tokens_repo_dir", "")
    control = os.path.join(repo, st["control_json"].format(donor=st["control_donor"]))
    wanted = (("blender_exe", loc.get("blender_exe", "")), ("skeleton", skeleton or loc.get("rig_glb", "")), ("skin_tokens_repo_dir", repo_dir),
              ("skin_tokens_python", loc.get("skin_tokens_python", "")), ("ckpt", os.path.join(repo_dir, st["ckpt"])),
              ("vae_ckpt", os.path.join(repo_dir, st["vae_ckpt"])), ("control", control)) + ((("launcher", st["launcher"]),) if st.get("launcher") else ())
    return [f"{k}: {p or '(not set)'}" for k, p in wanted if not p or not os.path.exists(p)]


def bound_name(cfg, which):
    """File name of the bound GLB a binder writes into the asset's work folder."""
    return cfg["skin_tokens"]["bound_glb_name"] if which == "skin_tokens" else cfg["bind"]["bound_glb_name"]


def write_pointer(cfg, work, which, reason=""):
    """Record which binder produced the bound body of this asset, so later stages (assemble) read the right GLB. Returns the pointer path."""
    path = os.path.join(work, POINTER)
    row = {"binder_used": which, "glb": os.path.join(work, bound_name(cfg, which)), "fallback": bool(reason), "label": FALLBACK_LABEL if reason else (DONOR_LABEL if which == "donor" else "skin_tokens")}
    if reason:
        row["reason"] = reason
    os.makedirs(work, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(row, f, indent=2)
    return path


def resolve_bound(cfg, work):
    """The bound body GLB of an asset: the pointer's GLB when a bind recorded one and it exists, else the configured binder's file name."""
    path = os.path.join(work, POINTER)
    try:
        with open(path, encoding="utf-8") as f:
            glb = json.load(f).get("glb")
        if glb and os.path.exists(glb):
            return glb
    except (OSError, ValueError):
        pass
    return os.path.join(work, bound_name(cfg, binder(cfg)))
