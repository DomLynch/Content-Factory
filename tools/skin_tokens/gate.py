"""The SkinTokens bind gate (standard library only). Same numbers as tools/run_bind.py (config bind.gate) PLUS the relative-to-control clause that run_bind.py skips
(LESSONS 2026-11-01): p99 stretch <= control x stretch_factor and p99 compression ratio >= control x compression_factor, from config skin_tokens.relative."""


def load_control(path):
    import json
    with open(path, encoding="utf-8-sig") as f:
        c = json.load(f)
    if "p99_stretch" not in c or "p99_compression_ratio" not in c:
        raise ValueError(f"{path} has no p99_stretch / p99_compression_ratio")
    return c


def checks(m, gate, control, rel, export_report=None):
    """m = metrics2.py output (dict). Returns {check: bool}; every value must be True to pass."""
    w = m.get("weights", {})
    out = {
        "edges_measured_ge_min": m.get("edges_measured_ge_5mm", 0) > 0,
        "p99_stretch": m.get("p99_stretch", 9e9) <= gate["p99_stretch_max"],
        "p99_compression_ratio": m.get("p99_compression_ratio", -1) >= gate["p99_compression_ratio_min"],
        "zero_weight_vertices": w.get("zero_weight_vertices", 1) <= gate["zero_weight_vertices_max"],
        "max_influences": w.get("max_influences", 99) <= gate["max_influences"],
        "weight_sum_dev": w.get("weight_sum_max_dev") is not None and w["weight_sum_max_dev"] <= gate["weight_sum_tol"],
    }
    if control is None:
        out["relative_to_control"] = False
    else:
        out["relative_to_control"] = bool(m.get("p99_stretch", 9e9) <= control["p99_stretch"] * rel["stretch_factor"]
                                          and m.get("p99_compression_ratio", -1) >= control["p99_compression_ratio"] * rel["compression_factor"])
    if export_report is not None:
        out["rig_bones_kept"] = not export_report.get("bones_missing")
    return out


def failed(c):
    return [k for k, v in c.items() if not v]
