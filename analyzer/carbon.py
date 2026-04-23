"""
carbon.py
Carbon emission estimation for each rule/context.

Formula:
  carbon_kg = extra_time_seconds * LAMBDA_KWH_PER_SEC * INDIA_GRID_KG_PER_KWH

Constants:
  LAMBDA_KWH_PER_SEC:
    AWS publishes 0.0038 kWh per GB-hour for Lambda.
    At 128MB (0.125 GB): 0.0038 * 0.125 = 0.000475 kWh/hour
    Per second: 0.000475 / 3600 = 1.319e-7 kWh/second

  INDIA_GRID_KG_PER_KWH:
    India CEA (Central Electricity Authority) CO2 Baseline 2023
    = 0.82 kg CO2 per kWh
    Source: Official government emission factor, used in ESG reporting.

  DB_ROUND_TRIP_SEC: 0.05s (50ms) — standard same-AZ RDS latency
  PHP_OPS_PER_SEC: 100,000,000 — PHP runtime throughput
  COLLECTION_SIZE: 5000 — assumed Laravel Collection size for count()

All per-call values are multiplied by runs_per_day * 30 for monthly total.
"""

# ── Constants ─────────────────────────────────────────────────
LAMBDA_KWH_PER_SEC      = 1.319e-7    # AWS published, 128MB Lambda
INDIA_GRID_KG_PER_KWH   = 0.82        # India CEA CO2 Baseline 2023

DEFAULT_RUNS_PER_DAY     = 10_000
DAYS_PER_MONTH           = 30

# Workload constants (mirrors cost.py)
DB_ROUND_TRIP_SEC        = 0.05        # seconds per extra RDS query
ASSUMED_N_ROWS           = 100
ASSUMED_M_ROWS           = 50
ASSUMED_COLLECTION_SIZE  = 5_000
PHP_OPS_PER_SEC          = 100_000_000

# Rule 3 — extra processing time per context (seconds)
# Based on row count × (RDS read time per row + deserialization time)
# RDS extra read: 0.001s per row (100KB/100MBps)
# Lambda deser:   0.01s fixed overhead for extra payload
RULE3_EXTRA_TIME = {
    1: 1.01,   # C1 all() — 1000 rows
    2: 0.11,   # C2 get() — 100 rows
    3: 0.011,  # C3 first() — 1 row
    4: 0.21,   # C4 with() — 200 rows
    5: 10.01,  # C5 in loop — N×N rows
}


def _carbon_per_call(extra_seconds: float) -> float:
    """kg CO2 for one function call with given extra execution time."""
    return extra_seconds * LAMBDA_KWH_PER_SEC * INDIA_GRID_KG_PER_KWH


def _carbon_monthly(extra_seconds: float,
                    runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """kg CO2 per month."""
    return _carbon_per_call(extra_seconds) * runs_per_day * DAYS_PER_MONTH


# ── Rule 1 — N+1 ─────────────────────────────────────────────
def rule1_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    if context == 3:
        extra_queries = (ASSUMED_N_ROWS * ASSUMED_M_ROWS) - 2
    else:
        extra_queries = ASSUMED_N_ROWS - 1

    extra_seconds = extra_queries * DB_ROUND_TRIP_SEC
    return round(_carbon_monthly(extra_seconds, runs_per_day), 6)


# ── Rule 2 — count() recalculation ───────────────────────────
def rule2_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    N = ASSUMED_COLLECTION_SIZE
    extra_ops = (N * N) - N
    extra_seconds = extra_ops / PHP_OPS_PER_SEC
    return round(_carbon_monthly(extra_seconds, runs_per_day), 6)


# ── Rule 3 — SELECT * ─────────────────────────────────────────
def rule3_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    extra_seconds = RULE3_EXTRA_TIME.get(context, 0.11)
    return round(_carbon_monthly(extra_seconds, runs_per_day), 6)


# ── Public API ────────────────────────────────────────────────

def estimate_carbon(rule_id: str, context: int,
                    runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """
    Returns monthly carbon estimate in kg CO2 for a given rule/context.
    """
    if rule_id == "R1":
        kg = rule1_carbon(context, runs_per_day)
    elif rule_id == "R2":
        kg = rule2_carbon(context, runs_per_day)
    elif rule_id == "R3":
        kg = rule3_carbon(context, runs_per_day)
    else:
        kg = 0.0

    return {
        "carbon_kg_monthly": kg,
        "grid_intensity": INDIA_GRID_KG_PER_KWH,
        "source": "India CEA CO2 Baseline 2023",
        "note": "Modelled estimate. Actual emissions depend on workload profile.",
    }