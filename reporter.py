"""
reporter.py
Formats engine output into the JSON shape the VSCode extension expects.

Extension reads these fields per issue:
  rule_id, title, suggestion, line, weight, severity

Extension reads these summary fields:
  green_score, estimated_co2_kg, issues, total_operation_weight

Cost + carbon fields are attached per-issue so the extension
hover tooltip can render tier pressure and carbon projections
without any client-side calculation.
"""

from typing import Dict, Any


def format_response(engine_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes raw engine output and returns the final JSON response
    the VSCode extension will consume.
    """
    issues = []

    for f in engine_output["findings"]:
        issues.append({
            # ── Core fields extension reads ───────────────────
            "rule_id":    f["rule_id"],
            "title":      f["title"],
            "suggestion": f["suggestion"],
            "line":       f["line"],
            "weight":     f["weight"],
            "severity":   f["severity"],

            # ── Context + description ─────────────────────────
            "context":     f["context"],
            "description": f["description"],

            # ── Cost fields (EC2+RDS model, not Lambda) ───────
            "cost_usd_monthly":        f["cost_usd_monthly"],
            "cost_inr_monthly":        f["cost_inr_monthly"],
            "cost_breakdown":          f["cost_breakdown"],        # tier delta detail
            "is_throughput_degrader":  f["rule_id"] == "R2",

            # ── Carbon fields (CEA 2023, 0.708 kg/kWh) ───────
            "carbon_kg_monthly":       f["carbon_kg_monthly"],
            "carbon_projections":      f["carbon_projections"],    # 1x/10x/100x scale
        })

    return {
        # ── Summary fields extension reads ────────────────────
        "green_score":            engine_output["green_score"],
        "estimated_co2_kg":       engine_output["estimated_co2_kg"],
        "total_operation_weight": engine_output["total_operation_weight"],
        "issues":                 issues,

        # ── Extra summary fields ──────────────────────────────
        "total_cost_usd_monthly": engine_output["total_cost_usd_monthly"],
        "total_cost_inr_monthly": engine_output["total_cost_inr_monthly"],
        "total_findings":         len(issues),
        "runs_per_day":           engine_output["runs_per_day"],
    }