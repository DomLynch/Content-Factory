r"""Latest-models gate (plugin 0.1.27; standard library only). Dom's mandate: before ANY generation, the tool about to run is checked against its upstream and a run on
an installed version that is BEHIND upstream is refused, unless it carries --allow-stale "<reason>" (also written to the receipt).

Universal: no model name or number lives in code. The rows are config/config.json -> models.registry:

  {"name": "TRELLIS.2", "stages": ["trellis"],
   "installed": {"kind": "git", "local": "trellis_repo_dir"},          git       the commit of the clone at config.local.json[local]
                                                                        hf_dir    the commit of a model folder that is a git clone (else unknown = unverified)
                                                                        pip       {"package": "x", "python_local": "<config.local key of the venv python>"}
                                                                        pinned    {"version": "1.2.3"} written by hand (a binary with no version call); null = unknown
   "upstream":  {"kind": "github_head", "repo": "owner/name"}}         github_head   default-branch HEAD commit
                                                                        github_release  latest release tag
                                                                        hf            model-card revision (commit sha) of a Hugging Face repo id
                                                                        pypi          latest version of a package

A row is checked when the stage being started is in its `stages`. State per row: current, behind (refuse), unverified (installed version unknown, or upstream unreachable
and nothing cached: allowed, and written to the receipt as unverified). The upstream lookup is cached 24 h in state/models_upstream.json; with no network the cached value is
used and the check is marked verified:false (a cached "behind" still refuses). Git rows are current when the upstream commit is the installed commit or an ancestor of it; if the
upstream commit is not in the local clone the row is behind (git fetch the upstream first). Every error in the gate itself allows (fail open, like the other gates).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import time
import urllib.request

import cf_guard

TTL_S = 24 * 3600
TIMEOUT_S = 8
UA = {"User-Agent": "content-factory-models-gate", "Accept": "application/vnd.github+json"}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _cache_path():
    return cf_guard.HOME / "state" / "models_upstream.json"


def _load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c: dict) -> None:
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(c, indent=1), encoding="utf-8")
    except Exception:
        pass


def _get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_upstream(up: dict) -> str:
    """The upstream latest for one `upstream` block (network). Raises on any failure."""
    k = up["kind"]
    if k == "github_head":
        return _get_json(f"https://api.github.com/repos/{up['repo']}/commits/HEAD")["sha"]
    if k == "github_release":
        return _get_json(f"https://api.github.com/repos/{up['repo']}/releases/latest")["tag_name"]
    if k == "hf":
        return _get_json(f"https://huggingface.co/api/models/{up['repo']}")["sha"]
    if k == "pypi":
        return _get_json(f"https://pypi.org/pypi/{up['package']}/json")["info"]["version"]
    raise ValueError(f"unknown upstream kind {k}")


def upstream_latest(up: dict, now: float | None = None, fetch=None) -> tuple[str | None, str, str | None]:
    """(value, source, checked_utc): source is live, cache or none. Cached 24 h; a failed lookup falls back to the cached value (any age)."""
    now = time.time() if now is None else now
    key = json.dumps(up, sort_keys=True)
    cache = _load_cache()
    hit = cache.get(key)
    if hit and now - hit["t"] < TTL_S:
        return hit["value"], "cache", hit["utc"]
    try:
        value = (fetch or fetch_upstream)(up)
    except Exception:
        return (hit["value"], "cache", hit["utc"]) if hit else (None, "none", None)
    cache[key] = {"value": value, "t": now, "utc": _now()}
    _save_cache(cache)
    return value, "live", cache[key]["utc"]


def _git(repo, *args) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
        return r.returncode, r.stdout.strip()
    except Exception:
        return 1, ""


def _dir(inst: dict, local: dict):
    """The folder of a git / hf_dir row: config.local.json[local], else work_dir/<default_subdir> (the layout config.local.example.json documents)."""
    d = local.get(inst["local"])
    if not d and inst.get("default_subdir") and local.get("work_dir"):
        d = os.path.join(local["work_dir"], inst["default_subdir"])
    return d


def installed_version(inst: dict, local: dict) -> str | None:
    k = inst["kind"]
    if k in ("git", "hf_dir"):
        d = _dir(inst, local)
        if not d or not os.path.isdir(d):
            return None
        rc, out = _git(d, "rev-parse", "HEAD")
        return out if rc == 0 and out else None
    if k == "pip":
        py = local.get(inst["python_local"])
        if not py or not os.path.isfile(py):
            return None
        try:
            r = subprocess.run([py, "-c", f"import importlib.metadata as m;print(m.version({inst['package']!r}))"], capture_output=True, text=True, timeout=30)
            return r.stdout.strip() or None if r.returncode == 0 else None
        except Exception:
            return None
    if k == "pinned":
        return inst.get("version") or None
    return None


def _ver(s: str):
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums) if nums else None


def compare(row: dict, installed: str | None, latest: str | None, local: dict) -> tuple[str, str]:
    """(state, reason): current, behind or unverified."""
    if installed is None:
        return "unverified", "installed version unknown (set the local path, or installed.version for a pinned row)"
    if latest is None:
        return "unverified", "upstream unreachable and nothing cached"
    if installed == latest:
        return "current", ""
    kind = row["installed"]["kind"]
    if kind in ("git", "hf_dir"):
        d = _dir(row["installed"], local)
        if _git(d, "cat-file", "-e", latest + "^{commit}")[0] != 0:
            return "behind", f"upstream commit {latest[:10]} is not in the local clone: git -C {d} fetch the upstream, or the clone is behind it"
        if _git(d, "merge-base", "--is-ancestor", latest, installed)[0] == 0:
            return "current", ""
        return "behind", f"installed {installed[:10]} does not contain upstream {latest[:10]}"
    a, b = _ver(installed), _ver(latest)
    if a is not None and b is not None:
        return ("current", "") if a >= b else ("behind", f"installed {installed} < upstream {latest}")
    return "behind", f"installed {installed} != upstream {latest}"


def registry(cfg: dict) -> list[dict]:
    return list(((cfg.get("models") or {}).get("registry")) or [])


def check(cfg: dict, stages, fetch=None) -> list[dict]:
    """One report row per registry row whose stages include any of `stages`. Never raises."""
    out = []
    local = cfg.get("local") or {}
    want = set([stages] if isinstance(stages, str) else stages)
    for row in registry(cfg):
        if not want & set(row.get("stages") or []):
            continue
        try:
            inst = installed_version(row["installed"], local)
            latest, source, utc = upstream_latest(row["upstream"], fetch=fetch)
            state, why = compare(row, inst, latest, local)
        except Exception as e:
            inst = latest = source = utc = None
            state, why = "unverified", f"check failed: {e!r}"[:200]
        out.append({"name": row.get("name"), "stages": row.get("stages"), "installed": inst, "upstream": latest, "upstream_checked_utc": utc,
                    "source": source, "verified": source == "live" and inst is not None, "state": state, "reason": why})
    return out


def refusals(report: list[dict]) -> list[str]:
    return [f"MODELS GATE: {r['name']} is behind upstream ({r['reason']}). Update it, or run with --allow-stale \"<reason>\" (written to the receipt). "
            f"installed {str(r['installed'])[:12]}, upstream {str(r['upstream'])[:12]} checked {r['upstream_checked_utc']} ({r['source']})."
            for r in report if r["state"] == "behind"]
