"""
engine.py
Orchestrates the full analysis pipeline:
  1. Parse PHP source with tree-sitter
  2. Run all three rules
  3. Attach cost + carbon to each finding
  4. Compute green score
"""

from analyzer.tree_sitter_setup import parse_php
from analyzer.rules import rule1_n1_query, rule2_count_recalc, rule3_select_star
from analyzer.cost import estimate_cost
from analyzer.carbon import estimate_carbon

# Weight → score mapping
# Total weight budget: a file with all worst-case findings would be ~360
# Green score = 100 - (total_weight / MAX_WEIGHT * 100), floored at 0
MAX_WEIGHT = 300


def compute_green_score(total_weight: int) -> int:
    score = 100 - int((total_weight / MAX_WEIGHT) * 100)
    return max(0, min(100, score))


def analyze(source_code: str, runs_per_day: int = 10_000) -> dict:
    """
    Main entry point. Takes PHP source as string.
    Returns structured findings dict ready for reporter.
    """
    source_bytes = source_code.encode("utf-8") if isinstance(source_code, str) else source_code
    tree = parse_php(source_bytes)

    # Run all rules
    findings_raw = []
    findings_raw.extend(rule1_n1_query.detect(tree, source_bytes))
    findings_raw.extend(rule2_count_recalc.detect(tree, source_bytes))
    findings_raw.extend(rule3_select_star.detect(tree, source_bytes))

    # Sort by line number for clean output
    findings_raw.sort(key=lambda f: f["line"])

    # Attach cost + carbon to each finding
    findings = []
    total_weight = 0
    total_carbon = 0.0
    total_cost = 0.0

    for f in findings_raw:
        cost_data   = estimate_cost(f["rule_id"], f["context"], runs_per_day)
        carbon_data = estimate_carbon(f["rule_id"], f["context"], runs_per_day)

        total_weight += f["weight"]
        total_carbon += carbon_data["carbon_kg_monthly"]
        total_cost   += cost_data["cost_usd_monthly"]

        findings.append({
            "rule_id":            f["rule_id"],
            "context":            f["context"],
            "line":               f["line"],
            "severity":           f["severity"],
            "weight":             f["weight"],
            "title":              f["title"],
            "description":        f["description"],
            "suggestion":         f["suggestion"],
            "cost_usd_monthly":   cost_data["cost_usd_monthly"],
            "cost_inr_monthly":   cost_data["cost_inr_monthly"],
            "carbon_kg_monthly":  carbon_data["carbon_kg_monthly"],
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
    }