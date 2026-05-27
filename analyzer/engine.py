"""
engine.py
Orchestrates the full analysis pipeline:
  1. Parse PHP source with tree-sitter
  2. Run all three rules
  3. Apply folder-context severity modifier
  4. Attach cost + carbon to each finding
  5. Compute green score
"""

from analyzer.tree_sitter_setup import parse_php
from analyzer.rules import rule1_n1_query, rule2_count_recalc, rule3_select_star
from analyzer.cost import estimate_cost
from analyzer.carbon import estimate_carbon
from analyzer.utils.severity_modifier import apply as apply_severity_modifier

MAX_WEIGHT = 300


def get_frequency_from_path(file_path: str) -> int:
    """
    Auto-detect realistic runs_per_day from file path.
    Based on Taxina traffic data: 700 completed rides/day, Mumbai region.

    Replaces flat 10k/day hardcode with context-aware frequency.
    Long term: replace with CloudWatch historical data per endpoint.
    """
    path = file_path.lower().replace("\\", "/")

    # ── Console scheduled commands ────────────────────────────
    if "console" in path or "command" in path:
        if "endinactive" in path:   return 1440   # everyMinute
        if "split" in path:         return 1      # daily 00:01
        if "archive" in path:       return 1      # daily 00:10
        return 24                                  # default hourly

    # ── Listeners ─────────────────────────────────────────────
    if "listener" in path:          return 1400   # ~2 events per ride

    # ── Jobs ──────────────────────────────────────────────────
    if "job" in path:               return 700    # one per completed ride

    # ── Admin / Web — low traffic ─────────────────────────────
    if "admin" in path:             return 50
    if "/web/" in path:             return 50

    # ── API Controllers ───────────────────────────────────────
    if "auth" in path:              return 200    # login per session
    if "user" in path:              return 700    # every ride request
    if "driver" in path:            return 700    # every ride request
    if "payment" in path:           return 500    # completed rides
    if "transaction" in path:       return 500    # completed rides
    if "subscription" in path:      return 200    # driver subscription checks
    if "outstation" in path:        return 70     # ~10% of rides
    if "partner" in path:           return 50     # admin level
    if "bob" in path:               return 500    # BOB payment flow

    # ── Helpers / Services / Models ───────────────────────────
    # inherit dominant calling controller frequency
    if "helper" in path:            return 700
    if "service" in path:           return 700
    if "model" in path:             return 700
    if "repository" in path:        return 700
    if "trait" in path:             return 700
    if "scope" in path:             return 700

    # ── Default ───────────────────────────────────────────────
    return 700


def compute_green_score(total_weight: int) -> int:
    score = 100 - int((total_weight / MAX_WEIGHT) * 100)
    return max(0, min(100, score))


def analyze(source_code: str, runs_per_day: int = None, file_path: str = "") -> dict:
    """
    Main entry point. Takes PHP source as string and optional file path.
    file_path is used for:
      - folder-context severity adjustment
      - auto-detecting realistic runs_per_day frequency

    If runs_per_day is explicitly passed, it overrides the auto-detection.
    Returns structured findings dict ready for reporter.
    """
    # auto-detect frequency from file path if not explicitly provided
    if runs_per_day is None:
        runs_per_day = get_frequency_from_path(file_path)

    source_bytes = source_code.encode("utf-8") if isinstance(source_code, str) else source_code
    tree = parse_php(source_bytes)

    # run all rules
    findings_raw = []
    findings_raw.extend(rule1_n1_query.detect(tree, source_bytes))
    findings_raw.extend(rule2_count_recalc.detect(tree, source_bytes))
    findings_raw.extend(rule3_select_star.detect(tree, source_bytes))

    # sort by line number
    findings_raw.sort(key=lambda f: f["line"])

    # apply folder-context severity modifier
    findings_raw = apply_severity_modifier(findings_raw, file_path)

    # attach cost + carbon to each finding
    findings = []
    total_weight = 0
    total_carbon = 0.0
    total_cost   = 0.0

    for f in findings_raw:
        cost_data   = estimate_cost(f["rule_id"], f["context"], runs_per_day)
        carbon_data = estimate_carbon(f["rule_id"], f["context"], runs_per_day)

        total_weight += f["weight"]
        total_carbon += carbon_data["carbon_kg_monthly"]
        total_cost   += cost_data["cost_usd_monthly"]

        findings.append({
            # ── Core fields ───────────────────────────────────
            "rule_id":     f["rule_id"],
            "context":     f["context"],
            "line":        f["line"],
            "severity":    f["severity"],
            "weight":      f["weight"],
            "title":       f["title"],
            "description": f["description"],
            "suggestion":  f["suggestion"],

            # ── Severity note (present only if downgraded) ────
            "severity_note": f.get("severity_note", ""),

            # ── Cost fields (EC2+RDS model) ───────────────────
            "cost_usd_monthly":  cost_data["cost_usd_monthly"],
            "cost_inr_monthly":  cost_data["cost_inr_monthly"],
            "cost_breakdown":    cost_data["breakdown"],

            # ── Carbon fields (CEA 2023, 0.708 kg/kWh) ───────
            "carbon_kg_monthly":  carbon_data["carbon_kg_monthly"],
            "carbon_projections": carbon_data["projections"],
        })

    green_score = compute_green_score(total_weight)

    return {
        "findings":               findings,
        "total_operation_weight": total_weight,
        "green_score":            green_score,
        "estimated_co2_kg":       round(total_carbon, 6),
        "total_cost_usd_monthly": round(total_cost, 4),
        "total_cost_inr_monthly": round(total_cost * 84, 2),
        "runs_per_day":           runs_per_day,
        "file_path":              file_path,
    }