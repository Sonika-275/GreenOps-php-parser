"""
carbon.py
Carbon emission estimation for each rule/context.

Target stack: Laravel on EC2 + RDS (ap-south-1)

Formula:
  carbon_kg = extra_energy_kwh * INDIA_GRID_KG_PER_KWH

Energy constant:
  EC2_KWH_PER_SEC:
    AWS publishes average server utilisation-adjusted PUE of ~1.2 for ap-south-1.
    A general-purpose EC2 instance (m5/t3 class) draws ~0.01 kWh per vCPU-hour
    under moderate load (SPECpower 2023, AWS sustainability report).
    Per second per vCPU: 0.01 / 3600 = 2.778e-6 kWh/second
    This replaces the Lambda-specific constant from the previous model.
    Lambda billed energy ≠ EC2 consumed energy — EC2 is always-on.

  RDS_KWH_PER_IO:
    RDS I/O energy is dominated by storage (NVMe SSD) + DB compute.
    Estimated at ~0.000001 kWh per I/O operation (conservative, from
    AWS RDS sustainability disclosures and academic DB benchmarks).

  INDIA_GRID_KG_PER_KWH:
    India CEA (Central Electricity Authority) CO2 Baseline 2023
    = 0.82 kg CO2 per kWh
    Source: Official government emission factor, used in ESG reporting.

Notes:
  - R2 (count()) retains a carbon value — CPU waste burns electricity
    even if it doesn't generate a direct AWS bill line.
  - R3 RULE3_EXTRA_TIME values are adjusted: Lambda deserialisation
    overhead removed; only RDS read time is counted.
"""

# ── Constants ─────────────────────────────────────────────────
EC2_KWH_PER_SEC         = 2.778e-6    # kWh per second, m5/t3 class EC2 vCPU
RDS_KWH_PER_IO          = 1.0e-6      # kWh per RDS I/O operation
INDIA_GRID_KG_PER_KWH   = 0.82        # India CEA CO2 Baseline 2023

DEFAULT_RUNS_PER_DAY     = 10_000
DAYS_PER_MONTH           = 30

# Workload constants (mirrors cost.py)
DB_ROUND_TRIP_SEC        = 0.05        # seconds per extra RDS query
ASSUMED_N_ROWS           = 100
ASSUMED_M_ROWS           = 50
ASSUMED_COLLECTION_SIZE  = 5_000
PHP_OPS_PER_SEC          = 100_000_000
RDS_PAGE_SIZE_BYTES      = 8192
EXTRA_COLUMNS            = 20
AVG_BYTES_PER_COLUMN     = 50

# Rule 3 — extra RDS read time per context (seconds)
# Based on row count × RDS read time per row (0.001s per row, same-AZ)
# Lambda deserialisation overhead removed — EC2 absorbs this into instance CPU.
RULE3_EXTRA_TIME = {
    1: 1.0,    # C1 all() — 1000 rows × 0.001s
    2: 0.1,    # C2 get() — 100 rows × 0.001s
    3: 0.001,  # C3 first() — 1 row × 0.001s
    4: 0.2,    # C4 with() — 200 rows × 0.001s
    5: 10.0,   # C5 in loop — N×N rows × 0.001s
}


def _carbon_from_time(extra_seconds: float,
                      runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """kg CO2 per month from extra EC2 CPU/wait time."""
    energy_kwh = extra_seconds * EC2_KWH_PER_SEC
    monthly_energy = energy_kwh * runs_per_day * DAYS_PER_MONTH
    return monthly_energy * INDIA_GRID_KG_PER_KWH


def _carbon_from_ios(extra_ios: int,
                     runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """kg CO2 per month from extra RDS I/O operations."""
    energy_kwh = extra_ios * RDS_KWH_PER_IO
    monthly_energy = energy_kwh * runs_per_day * DAYS_PER_MONTH
    return monthly_energy * INDIA_GRID_KG_PER_KWH


# ── Rule 1 — N+1 ─────────────────────────────────────────────
# Energy sources: EC2 wait time (blocked on DB) + RDS I/O energy
def rule1_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    if context == 3:
        extra_queries = (ASSUMED_N_ROWS * ASSUMED_M_ROWS) - 2
    else:
        extra_queries = ASSUMED_N_ROWS - 1

    extra_seconds = extra_queries * DB_ROUND_TRIP_SEC
    ec2_carbon = _carbon_from_time(extra_seconds, runs_per_day)
    rds_carbon = _carbon_from_ios(extra_queries, runs_per_day)
    return round(ec2_carbon + rds_carbon, 6)


# ── Rule 2 — count() recalculation ───────────────────────────
# Energy source: EC2 CPU only — no DB involved.
# Unlike cost.py (where EC2 CPU has no bill line),
# carbon is still real: wasted CPU cycles burn electricity.
def rule2_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    N = ASSUMED_COLLECTION_SIZE
    extra_ops = (N * N) - N
    extra_seconds = extra_ops / PHP_OPS_PER_SEC
    return round(_carbon_from_time(extra_seconds, runs_per_day), 6)


# ── Rule 3 — SELECT * ─────────────────────────────────────────
# Energy sources: RDS I/O energy (extra pages read) + EC2 time (extra read wait)
# Data transfer energy is negligible same-AZ — omitted.
def rule3_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    extra_seconds = RULE3_EXTRA_TIME.get(context, 0.1)

    # Also count RDS I/O energy from extra pages
    row_counts = {1: 1000, 2: 100, 3: 1, 4: 200, 5: ASSUMED_N_ROWS * ASSUMED_N_ROWS}
    rows = row_counts.get(context, 100)
    extra_bytes = rows * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN
    extra_ios = max(1, extra_bytes // RDS_PAGE_SIZE_BYTES)

    ec2_carbon = _carbon_from_time(extra_seconds, runs_per_day)
    rds_carbon = _carbon_from_ios(extra_ios, runs_per_day)
    return round(ec2_carbon + rds_carbon, 6)


# ── Public API ────────────────────────────────────────────────

def estimate_carbon(rule_id: str, context: int,
                    runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """
    Returns monthly carbon estimate in kg CO2 for a given rule/context.

    R2 returns a non-zero carbon value even though it has no AWS bill line —
    wasted CPU still consumes electricity and emits CO2.
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
        "source": "India CEA CO2 Baseline 2023 + AWS sustainability report",
        "note": "Modelled estimate for EC2+RDS stack. Actual emissions depend on workload profile.",
    }