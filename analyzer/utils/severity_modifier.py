"""
severity_modifier.py
Adjusts finding severity based on file path (folder context).

Rationale:
  The same inefficiency has different cost impact depending on where it lives.
  Admin/web controllers serve internal users — low traffic, low cost impact.
  API controllers serve production traffic — high frequency, high cost impact.
  Console commands depend on their schedule frequency.

Folder context rules:
  - Admin / Web controllers  → downgrade severity one level
  - API controllers          → keep severity as detected
  - Console commands         → keep severity as detected (frequency handled in cost model)
  - Models / Services        → keep severity as detected

Severity downgrade map:
  very high → high
  high      → medium
  medium    → low
  low       → low  (floor, no further downgrade)
"""

from typing import List, Dict, Any

# Folder patterns that indicate low-traffic admin/web context
LOW_TRAFFIC_PATTERNS = [
    "/Http/Controllers/Web/",
    "/Http/Controllers/Admin/",
    "\\Http\\Controllers\\Web\\",
    "\\Http\\Controllers\\Admin\\",
    "/controllers/web/",
    "/controllers/admin/",
]

# Severity downgrade map
SEVERITY_DOWNGRADE = {
    "very high": "high",
    "high":      "medium",
    "medium":    "low",
    "low":       "low",   # floor
}


def is_low_traffic_file(file_path: str) -> bool:
    """Check if the file belongs to a low-traffic admin/web context."""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/").lower()
    for pattern in LOW_TRAFFIC_PATTERNS:
        if pattern.replace("\\", "/").lower() in normalized:
            return True
    return False


def apply(findings: List[Dict[str, Any]], file_path: str) -> List[Dict[str, Any]]:
    """
    Apply folder-context severity adjustments to findings.
    Returns findings with updated severity values.
    """
    if not is_low_traffic_file(file_path):
        return findings  # no change for api/production files

    adjusted = []
    for finding in findings:
        f = finding.copy()
        original_severity = f.get("severity", "medium")
        f["severity"] = SEVERITY_DOWNGRADE.get(original_severity, original_severity)
        f["severity_note"] = (
            f"Severity downgraded from '{original_severity}' to '{f['severity']}' "
            f"— admin/web controller, low production traffic."
        )
        adjusted.append(f)

    return adjusted